"""What the replay worker plugs together, as one injectable value (``WorkerDeps``).

The defaults import the capture proxy (``ratchet.proxy.app``), the mock tool server
(``ratchet.mocktools.app``) and run bookkeeping (``ratchet.capture.runs``) inside the
functions that use them, so importing the worker stays cheap and tests can substitute small
fakes. ``load_deps("<module or /path/file.py>:<factory>")`` resolves a factory returning a
``WorkerDeps``; the worker CLI exposes it as ``--deps`` for integration tests (the person
who can pass CLI flags to the worker can already run code there, so this adds no access).
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast

from starlette.types import ASGIApp

from ratchet.config.settings import Settings
from ratchet.errors import ConfigError
from ratchet.models import EnvSnapshot, Tier, Trace
from ratchet.proxy.policy import SidecarPolicy
from ratchet.replay.harness.base import SandboxedHarness, get_adapter
from ratchet.replay.sandbox import Sandbox, get_sandbox
from ratchet.store import Store

__all__ = [
    "WorkerDeps",
    "RunMetaArgs",
    "FinalizeArgs",
    "MockAppArgs",
    "ProxyAppArgs",
    "default_deps",
    "load_deps",
]


@dataclass(frozen=True)
class RunMetaArgs:
    """CONTRACTS §2 ``traces/<run_id>/run.json`` fields."""

    run_id: str
    agent_tier: Tier
    seed: int
    frozen_time: datetime | None
    harness: str
    edit_id: str | None


@dataclass(frozen=True)
class FinalizeArgs:
    run_id: str
    task_input: str
    final_output: str
    env: EnvSnapshot
    agent_tier: Tier
    seed: int
    skills_invoked: list[str]
    workspace_before: Path
    workspace_after: Path
    golden_of: str
    candidate_commit: str | None
    world_dependent: bool = False
    task_label: str = ""
    error_code: str | None = None  # harness_failed | budget_exceeded → harness_ok=False


@dataclass(frozen=True)
class MockAppArgs:
    """Inputs for one golden's mock tool server (replay mode)."""

    settings: Settings
    store: Store
    cassette_root: Path
    overlay_root: Path | None  # world overlay (``world_ref``) consulted first
    frozen_time: datetime | None
    admin_token: str | None = None  # /log and /mode: the worker only, never the sandbox
    run_id: str | None = None  # the one run this sidecar serves
    # Host headers /mcp accepts besides loopback: the sandbox's connect host (DNS-rebinding
    # protection would otherwise refuse Hermes's MCP calls from inside the sandbox).
    mcp_allowed_hosts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProxyAppArgs:
    """Inputs for one golden's capture-proxy sidecar."""

    settings: Settings
    store: Store
    sidecar: SidecarPolicy


@dataclass(frozen=True)
class WorkerDeps:
    proxy_app: Callable[[ProxyAppArgs], ASGIApp]
    mock_app: Callable[[MockAppArgs], ASGIApp]
    write_run_meta: Callable[[Store, RunMetaArgs], None]
    finalize_trace: Callable[[Store, FinalizeArgs], Trace]
    sandbox_factory: Callable[[str, Settings], Sandbox] = lambda name, s: get_sandbox(name, s)
    adapter_factory: Callable[[str], SandboxedHarness] = get_adapter


def _attr(module: str, name: str) -> object:
    return getattr(importlib.import_module(module), name)


def _proxy_app(args: ProxyAppArgs) -> ASGIApp:
    from ratchet.proxy.app import create_app

    return create_app(settings=args.settings, store=args.store, sidecar=args.sidecar)


def _mock_app(args: MockAppArgs) -> ASGIApp:
    from ratchet.mocktools.app import create_app

    return create_app(
        args.settings,
        args.store,
        mode="replay",
        cassette_root=args.cassette_root,
        overlay_root=args.overlay_root,
        frozen_time=args.frozen_time,
        admin_token=args.admin_token,
        run_id=args.run_id,
        mcp_allowed_hosts=list(args.mcp_allowed_hosts),
    )


def _write_run_meta(store: Store, args: RunMetaArgs) -> None:
    from ratchet.capture.runs import RunMeta, write_run_meta

    meta = RunMeta(
        run_id=args.run_id,
        agent_tier=args.agent_tier,
        seed=args.seed,
        frozen_time=args.frozen_time,
        harness=args.harness,
        edit_id=args.edit_id,
        purpose="agent",
    )
    write_run_meta(store, meta)


def _finalize_trace(store: Store, args: FinalizeArgs) -> Trace:
    from ratchet.capture.runs import finalize_trace

    return finalize_trace(
        store,
        args.run_id,
        task_input=args.task_input,
        final_output=args.final_output,
        kind="replay",
        env=args.env,
        agent_tier=args.agent_tier,
        seed=args.seed,
        skills_invoked=args.skills_invoked,
        workspace_before=args.workspace_before,
        workspace_after=args.workspace_after,
        golden_of=args.golden_of,
        candidate_commit=args.candidate_commit,
        world_dependent=args.world_dependent,
        task_label=args.task_label,
        harness_ok=args.error_code is None,
        error_code=args.error_code,
    )


def default_deps() -> WorkerDeps:
    return WorkerDeps(
        proxy_app=_proxy_app,
        mock_app=_mock_app,
        write_run_meta=_write_run_meta,
        finalize_trace=_finalize_trace,
    )


def load_deps(target: str) -> WorkerDeps:
    """Resolve ``module:factory`` or ``/path/to/file.py:factory`` to a ``WorkerDeps``."""
    location, _, factory_name = target.rpartition(":")
    if not location or not factory_name:
        raise ConfigError("--deps must look like module:factory", code="config_error")
    if location.endswith(".py"):
        spec = importlib.util.spec_from_file_location("ratchet_worker_deps", location)
        if spec is None or spec.loader is None:
            raise ConfigError("cannot load --deps file", code="config_error")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        factory = getattr(module, factory_name)
    else:
        factory = _attr(location, factory_name)
    deps = cast(Callable[[], object], factory)()
    if not isinstance(deps, WorkerDeps):
        raise ConfigError("--deps factory did not return WorkerDeps", code="config_error")
    return deps
