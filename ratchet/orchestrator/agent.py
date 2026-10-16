"""Run one live agent task on the control plane (design §6; the input to the bless pipeline).

The capture proxy and the mock tool server (record mode, against a ``World``) run
in-process on ephemeral ports (``ratchet.replay.sidecars.ServerThread``); the harness
adapter (MinimalRunner by default) talks to them exactly as it does inside a replay
sandbox. Steps:

1. snapshot the workspace before the task → ``traces/<run_id>/workspace.tar.zst``
   (``env.workspace_ref``, resolved trace-relative by the replay packer);
2. write the run meta (tier, seed, frozen time) so the proxy and mock tools honor them;
3. run the adapter with ``proxy_url=<proxy>/v1`` (it calls ``/v1/r/<run_id>/...``);
4. ``finalize_trace(kind="live")`` (workspace diff → ``fs.write``/``fs.delete`` calls).
"""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ratchet import clock
from ratchet.capture.runs import RunMeta, finalize_trace, write_run_meta
from ratchet.config.settings import Settings
from ratchet.errors import GitError
from ratchet.log import get_logger
from ratchet.mocktools.world import World
from ratchet.models import EnvSnapshot, HarnessResult, Tier, Trace
from ratchet.replay.harness import get_adapter
from ratchet.replay.sidecars import ServerThread
from ratchet.store import Store
from ratchet.store.archive import pack_dir
from ratchet.util import new_id, sha256_hex
from ratchet.watcher.gitops import SkillsRepo

__all__ = ["ControlPlane", "control_plane", "run_task", "TASK_TIMEOUT_S"]

TASK_TIMEOUT_S = 300
_log = get_logger("agent")


@dataclass(frozen=True)
class ControlPlane:
    proxy: ServerThread
    mock: ServerThread

    @property
    def proxy_url(self) -> str:
        return f"{self.proxy.url}/v1"

    @property
    def mock_url(self) -> str:
        return self.mock.url


@contextlib.contextmanager
def control_plane(settings: Settings, store: Store, world: World) -> Iterator[ControlPlane]:
    """Proxy + mock tools (record mode, HTTP and MCP) on 127.0.0.1 ephemeral ports for live runs."""
    from ratchet.mocktools.app import create_app as mock_app
    from ratchet.proxy.app import create_app as proxy_app

    proxy = ServerThread(proxy_app(settings=settings, store=store), "127.0.0.1", name="proxy")
    # MCP on: Hermes reaches the mock tools only through /mcp.
    mock = ServerThread(
        mock_app(settings, store, mode="record", world=world, enable_mcp=True),
        "127.0.0.1",
        name="mock",
    )
    with proxy, mock:
        yield ControlPlane(proxy, mock)


def _snapshot(store: Store, run_id: str, workspace: Path, before: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(workspace, before, dirs_exist_ok=True)
    pack_dir(workspace, store.trace_dir(run_id) / "workspace.tar.zst")


def _skills_commit(repo_dir: Path) -> str:
    try:
        return SkillsRepo(repo_dir).main_head()
    except GitError:
        return "unknown"


def _run_adapter(
    plane: ControlPlane,
    *,
    harness: str,
    task_input: str,
    repo_dir: Path,
    workspace: Path,
    frozen: datetime,
    seed: int,
    tier: Tier,
    run_id: str,
) -> HarnessResult:
    adapter = get_adapter(harness)
    adapter.configure(
        proxy_url=plane.proxy_url,
        mock_tools_url=plane.mock_url,
        skills_dir=repo_dir,
        workspace=workspace,
        frozen_time=frozen,
        seed=seed,
        agent_tier=tier,
        run_id=run_id,
    )
    with clock.frozen(frozen):
        return adapter.run_task(task_input, timeout_s=TASK_TIMEOUT_S)


async def run_task(
    task_input: str,
    *,
    settings: Settings,
    store: Store,
    repo_dir: Path,
    world: World,
    frozen_time: datetime | None,
    seed: int,
    task_label: str = "",
    workspace: Path | None = None,
    plane: ControlPlane | None = None,
    agent_tier: Tier | None = None,
    harness: str | None = None,
) -> Trace:
    """Run ``task_input`` live (record mode) and return its finalized ``Trace(kind="live")``."""
    run_id = new_id("run")
    tier = agent_tier or settings.ratchet_agent_tier
    name = harness or settings.ratchet_harness
    frozen = frozen_time or world.frozen_time or clock.now()
    work = workspace or settings.ratchet_data_dir / "workspace"
    with tempfile.TemporaryDirectory(prefix="ratchet-before-") as tmp:
        before = Path(tmp) / "before"
        await asyncio.to_thread(_snapshot, store, run_id, work, before)
        meta = RunMeta(run_id=run_id, agent_tier=tier, seed=seed, frozen_time=frozen, harness=name)
        await asyncio.to_thread(write_run_meta, store, meta)
        await asyncio.to_thread(
            store.emit,
            "run.started",
            run_id,
            {"run_id": run_id, "task_input_hash": sha256_hex(task_input)[:16], "agent_tier": tier},
        )
        with contextlib.ExitStack() as stack:
            live = plane or stack.enter_context(control_plane(settings, store, world))
            result = await asyncio.to_thread(
                _run_adapter,
                live,
                harness=name,
                task_input=task_input,
                repo_dir=repo_dir,
                workspace=work,
                frozen=frozen,
                seed=seed,
                tier=tier,
                run_id=run_id,
            )
        trace = await asyncio.to_thread(
            _finalize,
            store,
            run_id,
            task_input,
            result,
            repo_dir,
            frozen,
            tier,
            seed,
            task_label,
            before,
            work,
        )
    await asyncio.to_thread(
        store.emit,
        "run.finished",
        run_id,
        {"run_id": run_id, "ok": result.exit_ok, "agent_tier": tier, "n_steps": len(trace.steps)},
    )
    _log.info("agent.run.done", run_id=run_id, ok=result.exit_ok, n_steps=len(trace.steps))
    return trace


def _finalize(
    store: Store,
    run_id: str,
    task_input: str,
    result: HarnessResult,
    repo_dir: Path,
    frozen: datetime,
    tier: Tier,
    seed: int,
    task_label: str,
    before: Path,
    after: Path,
) -> Trace:
    env = EnvSnapshot(
        workspace_ref="workspace.tar.zst",
        frozen_time=frozen,
        harness_version=result.harness_version,
        skills_commit=_skills_commit(repo_dir),
    )
    return finalize_trace(
        store,
        run_id,
        task_input=task_input,
        final_output=result.final_output,
        kind="live",
        env=env,
        agent_tier=tier,
        seed=seed,
        skills_invoked=result.skills_invoked,
        workspace_before=before,
        workspace_after=after,
        task_label=task_label,
        harness_ok=result.exit_ok,
        error_code=None if result.exit_ok else "harness_failed",
    )
