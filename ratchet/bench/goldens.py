"""Record and bless the bench goldens (dataset construction, step 1).

Each task runs on the control plane exactly as the demo's goldens do (design §6):
``orchestrator.agent.run_task`` → MinimalRunner → in-process proxy (live or simulator, per
settings) → mock tools in record mode against ``bench/world``, frozen at the world's time, seed
= a stable hash of the task id, the task's workspace copied in. The run is then blessed with
``orchestrator.bless.bless`` (PII pass, Nano enrichment). Idempotent: the manifest
(``<work>/data/bench/manifest.json``) maps task ids to golden run ids and a task whose golden
exists is skipped. The three canaries are written to ``data/canaries.json`` for selection.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ratchet.bench.dataset import BenchTask
from ratchet.bench.env import BenchEnv
from ratchet.llm.client import ModelClient
from ratchet.log import get_logger
from ratchet.mocktools.world import World
from ratchet.orchestrator.agent import ControlPlane, control_plane, run_task
from ratchet.orchestrator.bless import bless
from ratchet.orchestrator.context import CANARIES_FILE, InProcessProxy
from ratchet.orchestrator.demo import DEFAULT_FROZEN, task_seed
from ratchet.store.base import write_json_atomic

__all__ = ["GoldenReport", "record_goldens", "golden_ids"]

_log = get_logger("bench")


@dataclass
class GoldenReport:
    recorded: int = 0
    skipped: int = 0
    usd: float = 0.0
    misrouted: list[str] = field(default_factory=list)  # task ids whose golden ran another skill
    manifest: dict[str, str] = field(default_factory=dict)


def _is_golden(env: BenchEnv, run_id: str) -> bool:
    if not env.store.has_trace(run_id):
        return False
    trace = env.store.load_trace(run_id)
    return trace.kind == "golden" and trace.archived_reason is None


def _archive(env: BenchEnv, run_id: str) -> None:
    trace = env.store.load_trace(run_id)
    env.store.save_trace(trace.model_copy(update={"archived_reason": "reseeded"}))


async def _record_one(
    env: BenchEnv, task: BenchTask, world: World, plane: ControlPlane, client: ModelClient
) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="ratchet-bench-ws-") as tmp:
        workspace = Path(tmp) / "workspace"
        if task.workspace:
            await asyncio.to_thread(
                shutil.copytree, env.paths.workspaces / task.workspace, workspace
            )
        trace = await run_task(
            task.task_input,
            settings=env.settings,
            store=env.store,
            repo_dir=env.repo.main_dir,
            world=world,
            frozen_time=world.frozen_time or DEFAULT_FROZEN,
            seed=task_seed(task.task_id),
            task_label=task.label,
            workspace=workspace,
            plane=plane,
        )
    golden = await bless(env.store, env.settings, trace.run_id, client=client)
    return golden.run_id, list(golden.skills_invoked)


async def record_goldens(
    env: BenchEnv,
    tasks: Sequence[BenchTask],
    *,
    force: bool = False,
    on_task: Callable[[BenchTask, str], None] | None = None,
) -> GoldenReport:
    """Record + bless every task without a golden; see the module docstring."""
    await asyncio.to_thread(env.ensure_repo)
    manifest = await asyncio.to_thread(env.manifest)
    world = await asyncio.to_thread(World.load, env.paths.world)
    spent_before = await asyncio.to_thread(env.spent_usd)
    report = GoldenReport()
    proxy = InProcessProxy(env.settings, env.store)
    client = ModelClient(run_id="bless_bench", settings=env.settings, http=proxy.client)
    try:
        with control_plane(env.settings, env.store, world) as plane:
            for task in tasks:
                existing = manifest.get(task.task_id)
                if existing is not None and await asyncio.to_thread(_is_golden, env, existing):
                    if not force:
                        report.skipped += 1
                        continue
                    await asyncio.to_thread(_archive, env, existing)
                run_id, invoked = await _record_one(env, task, world, plane, client)
                manifest[task.task_id] = run_id
                await asyncio.to_thread(env.save_manifest, manifest)
                if (task.expected_skill or None) != (invoked[0] if invoked else None):
                    report.misrouted.append(task.task_id)
                report.recorded += 1
                if on_task is not None:
                    on_task(task, run_id)
    finally:
        await proxy.aclose()
    canaries = [manifest[t.task_id] for t in tasks if t.canary and t.task_id in manifest]
    await asyncio.to_thread(write_json_atomic, env.store.data_dir / CANARIES_FILE, canaries)
    report.usd = round(await asyncio.to_thread(env.spent_usd) - spent_before, 6)
    report.manifest = manifest
    _log.info("bench.goldens", recorded=report.recorded, skipped=report.skipped)
    return report


def golden_ids(env: BenchEnv, task_ids: Sequence[str]) -> list[str]:
    manifest = env.manifest()
    return [manifest[t] for t in task_ids if t in manifest]
