"""Replay one golden inside a sandbox (the per-golden lifecycle).

prepare (workspace + skills to scratch) → run meta → sidecars (proxy + mock tools, in
process, on ephemeral ports) → sandbox create → harness run with the wall-clock cap →
collect (harness result, unexpected side effects, workspace diff via ``finalize_trace``)
→ teardown. Failures before the harness produced anything are ``sandbox_failed`` (no
trace); a crashed, timed-out or budget-stopped harness still yields a trace with
``harness_ok=False`` so its log is attached to the pair.
"""

from __future__ import annotations

import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import ValidationError

from ratchet.config.settings import Settings
from ratchet.errors import SandboxError
from ratchet.llm.client import HEADER
from ratchet.log import get_logger
from ratchet.models import EnvSnapshot, HarnessResult, ReplayBatch, ToolCall, Trace
from ratchet.proxy.policy import SidecarPolicy
from ratchet.replay.deps import FinalizeArgs, MockAppArgs, ProxyAppArgs, RunMetaArgs, WorkerDeps
from ratchet.replay.harness.base import EXIT_BUDGET, EXIT_OK, HostPaths
from ratchet.replay.sandbox import Endpoint, Sandbox, faketime_env
from ratchet.replay.sidecars import ServerThread
from ratchet.store import Store
from ratchet.store.archive import unpack
from ratchet.util import new_id

__all__ = ["BatchContext", "GoldenOutcome", "replay_golden", "parse_unexpected"]

_log = get_logger("replay.worker")


@dataclass(frozen=True)
class BatchContext:
    spec: ReplayBatch
    inputs: Path
    output: Path
    scratch: Path
    store: Store  # worker-scoped store (scratch); proxy and mock tools record into it
    settings: Settings
    deps: WorkerDeps
    cassette_root: Path
    skills_tar: Path
    overlay_root: Path | None


@dataclass(frozen=True)
class GoldenOutcome:
    golden_run_id: str
    trace: Trace | None
    unexpected: list[ToolCall]
    error_code: str | None  # sandbox_failed | harness_failed | budget_exceeded


@dataclass
class _Run:
    """Mutable per-golden state; everything here is torn down in ``finally``."""

    run_id: str
    root: Path
    host: HostPaths
    before: Path
    sandbox: Sandbox | None = None
    proxy: ServerThread | None = None
    mock: ServerThread | None = None
    admin_token: str = ""  # sidecar admin routes; never placed in the sandbox env


def parse_unexpected(payload: object) -> list[ToolCall]:
    """``GET /log`` → ToolCalls with ``side_effect != read`` (the ReplayResult side effects)."""
    items: object = payload
    if isinstance(payload, dict):
        items = next((payload[k] for k in ("items", "unexpected") if k in payload), [])
    calls: list[ToolCall] = []
    for item in items if isinstance(items, list) else []:
        try:
            call = ToolCall.model_validate(item)
        except ValidationError:
            continue
        if call.side_effect != "read":
            calls.append(call)
    return calls


def _proxy_settings(ctx: BatchContext, golden: Trace) -> Settings:
    update: dict[str, object] = {
        "ratchet_data_dir": ctx.store.data_dir,
        "ratchet_agent_tier": golden.agent_tier,
    }
    upstream = ctx.settings.ratchet_tf_proxy_upstream
    if upstream:
        update["nebius_tf_base_url"] = upstream if upstream.endswith("/") else upstream + "/"
    return ctx.settings.model_copy(update=update)


def _prepare(ctx: BatchContext, golden: Trace, run: _Run) -> None:
    workspace = ctx.inputs / "workspaces" / f"{golden.run_id}.tar.zst"
    if not workspace.is_file():
        raise SandboxError("workspace snapshot missing from the batch", code="sandbox_failed")
    for directory in (run.host.work, run.host.skills, run.host.io, run.before):
        directory.mkdir(parents=True, exist_ok=True)
    try:
        unpack(workspace, run.before)
        unpack(workspace, run.host.work)
        unpack(ctx.skills_tar, run.host.skills)
    except (OSError, ValueError) as exc:
        raise SandboxError("could not materialize the sandbox", code="sandbox_failed") from exc
    meta = RunMetaArgs(
        run_id=run.run_id,
        agent_tier=golden.agent_tier,
        seed=golden.seed,
        frozen_time=golden.env.frozen_time,
        harness=ctx.spec.harness,
        edit_id=ctx.spec.edit_id,
    )
    ctx.deps.write_run_meta(ctx.store, meta)


def _start(
    ctx: BatchContext, golden: Trace, run: _Run
) -> tuple[int, HarnessResult, list[ToolCall]]:
    sandbox = run.sandbox = ctx.deps.sandbox_factory(ctx.spec.sandbox, ctx.settings)
    preflight = getattr(sandbox, "preflight", None)
    if preflight is not None:  # DockerSandbox: network exists, gateway bindable
        preflight()
    hint = getattr(sandbox, "bind_hint", None)
    policy = SidecarPolicy(
        agent_tier=golden.agent_tier,
        allowance_usd=ctx.spec.budget_allowance_usd,
        admin_token=run.admin_token,
    )
    proxy_app = ctx.deps.proxy_app(ProxyAppArgs(_proxy_settings(ctx, golden), ctx.store, policy))
    run.proxy = ServerThread(proxy_app, sandbox.bind_host, name="proxy", hint=hint).start()
    mock_settings = ctx.settings.model_copy(update={"ratchet_data_dir": ctx.store.data_dir})
    mock_args = MockAppArgs(
        mock_settings,
        ctx.store,
        ctx.cassette_root,
        ctx.overlay_root,
        golden.env.frozen_time,
        admin_token=run.admin_token,
        run_id=run.run_id,
        mcp_allowed_hosts=(f"{sandbox.connect_host}:*",),
    )
    mock_app = ctx.deps.mock_app(mock_args)
    run.mock = ServerThread(mock_app, sandbox.bind_host, name="mock", hint=hint).start()
    proxy_url = f"http://{sandbox.connect_host}:{run.proxy.port}/v1"
    mock_url = f"http://{sandbox.connect_host}:{run.mock.port}"
    inside = sandbox.paths(run.host)
    adapter = ctx.deps.adapter_factory(ctx.spec.harness)
    adapter.configure(
        proxy_url=proxy_url,
        mock_tools_url=mock_url,
        skills_dir=Path(inside.skills),
        workspace=Path(inside.work),
        frozen_time=golden.env.frozen_time,
        seed=golden.seed,
        agent_tier=golden.agent_tier,
        run_id=run.run_id,
    )
    timeout_s = _timeout(ctx.spec)
    plan = adapter.plan(
        golden.task_input, timeout_s=timeout_s, host=run.host, inside=inside, python=sandbox.python
    )
    env = {
        "RATCHET_RUN_ID": run.run_id,
        "RATCHET_AGENT_TIER": golden.agent_tier,
        "RATCHET_PROXY_URL": proxy_url,
        "RATCHET_MOCK_TOOLS_URL": mock_url,
        "OPENAI_BASE_URL": f"{proxy_url}/r/{run.run_id}",
        **faketime_env(golden.env.frozen_time, sandbox.faketime_lib),
        **plan.env,
    }
    egress = [
        Endpoint(sandbox.bind_host, run.proxy.port),
        Endpoint(sandbox.bind_host, run.mock.port),
    ]
    sandbox.create(run.host.work, run.host.skills, env, io_dir=run.host.io, egress=egress)
    code, out, err = sandbox.run(plan.argv, timeout_s)
    result = adapter.collect(code, out, err, host=run.host)
    _save_logs(ctx.store, run.run_id, out, err)
    return code, result, _fetch_unexpected(run.mock, run.run_id, run.admin_token)


def _timeout(spec: ReplayBatch) -> float:
    return spec.timeout_s / max(len(spec.golden_run_ids), 1)


def _save_logs(store: Store, run_id: str, out: str, err: str) -> None:
    logs = store.trace_dir(run_id) / "harness"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "stdout.txt").write_text(out, encoding="utf-8")
    (logs / "stderr.txt").write_text(err, encoding="utf-8")


def _fetch_unexpected(mock: ServerThread, run_id: str, admin_token: str) -> list[ToolCall]:
    headers = {HEADER.ADMIN_TOKEN: admin_token}
    try:
        response = httpx.get(
            f"{mock.url}/log", params={"run_id": run_id}, headers=headers, timeout=10.0
        )
        return parse_unexpected(response.json())
    except (httpx.HTTPError, ValueError):
        _log.warning("replay.mock_log.unavailable", run_id=run_id)
        return []


def _teardown(run: _Run) -> None:
    for server in (run.proxy, run.mock):
        if server is not None:
            server.stop()
    if run.sandbox is not None:
        try:
            run.sandbox.destroy()
        except SandboxError:
            _log.warning("replay.sandbox.destroy_failed", run_id=run.run_id)
    shutil.rmtree(run.root, ignore_errors=True)


def replay_golden(ctx: BatchContext, golden: Trace) -> GoldenOutcome:
    """Replay ``golden`` once; never raises for a per-golden failure."""
    run_id = new_id("run")
    root = ctx.scratch / run_id
    host = HostPaths(work=root / "work", skills=root / "skills", io=root / "io")
    run = _Run(run_id=run_id, root=root, host=host, before=root / "before")
    run.admin_token = secrets.token_urlsafe(24)
    try:
        _prepare(ctx, golden, run)
        code, result, unexpected = _start(ctx, golden, run)
        error = None
        if code == EXIT_BUDGET:
            error = "budget_exceeded"
        elif code != EXIT_OK or not result.exit_ok:
            error = "harness_failed"
        env = EnvSnapshot(
            workspace_ref=golden.env.workspace_ref,
            frozen_time=golden.env.frozen_time,
            harness_version=result.harness_version,
            skills_commit=ctx.spec.candidate_commit or golden.env.skills_commit,
            world_ref=ctx.spec.world_ref,
        )
        args = FinalizeArgs(
            run_id=run_id,
            task_input=golden.task_input,
            final_output=result.final_output,
            env=env,
            agent_tier=golden.agent_tier,
            seed=golden.seed,
            skills_invoked=result.skills_invoked,
            workspace_before=run.before,
            workspace_after=run.host.work,
            golden_of=golden.run_id,
            candidate_commit=ctx.spec.candidate_commit or None,
            world_dependent=golden.world_dependent,
            task_label=golden.task_label,
            error_code=error,
        )
        trace = ctx.deps.finalize_trace(ctx.store, args)
        shutil.copytree(
            ctx.store.trace_dir(run_id), ctx.output / "replays" / run_id, dirs_exist_ok=True
        )
        _log.info("replay.golden.done", run_id=run_id, ok=error is None, code=code)
        return GoldenOutcome(golden.run_id, trace, unexpected, error)
    except SandboxError as exc:
        _log.warning("replay.golden.sandbox_failed", run_id=run_id, code=exc.code)
        return GoldenOutcome(golden.run_id, None, [], "sandbox_failed")
    finally:
        _teardown(run)
