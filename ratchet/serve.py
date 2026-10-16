"""``ratchet serve``: every control-plane service in one asyncio process (CONTRACTS §1).

- dashboard + API on ``ratchet_dashboard_host:ratchet_dashboard_port`` (8080), with
  ``proxy_headers`` so rate limits key on the real client IP behind the reverse proxy;
- capture proxy on 127.0.0.1:``ratchet_proxy_port`` (8081);
- mock tools on 127.0.0.1:``ratchet_mock_tools_port`` (8082): record mode against the demo
  world when it exists and the host is not public, otherwise replay mode;
- the orchestrator loop and the skills watcher.

SIGINT/SIGTERM stop the servers, then the orchestrator (in-flight edits resume on the next
start). The dashboard app is imported lazily; without ``ratchet.dashboard_api`` the
command fails with a clear ``config_error``.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Iterator
from typing import Any

import uvicorn
from fastapi import FastAPI

from ratchet.config.settings import Settings, public_mode_refusal
from ratchet.errors import ConfigError
from ratchet.log import get_logger
from ratchet.orchestrator.service import Orchestrator
from ratchet.store import Store

__all__ = ["serve", "build_apps", "QuietServer"]

_log = get_logger("serve")


class QuietServer(uvicorn.Server):
    """A uvicorn server that leaves signal handling to ``serve`` (several share one loop)."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def _dashboard(settings: Settings, store: Store, orchestrator: Orchestrator) -> FastAPI:
    try:
        from ratchet.dashboard_api.app import create_app
        from ratchet.dashboard_api.deps import ApiOptions
    except ImportError as exc:
        raise ConfigError(
            "ratchet.dashboard_api is not installed; `ratchet serve` needs it",
            code="config_error",
        ) from exc
    from ratchet.orchestrator.simulate import SimulateService

    async def orchestrator_up() -> bool:
        return orchestrator.running

    options = ApiOptions(
        simulate=SimulateService(settings, store, orchestrator),
        probes={"orchestrator": orchestrator_up},
    )
    app: FastAPI = create_app(settings, store, orchestrator, options=options)
    return app


def _mock(settings: Settings, store: Store) -> FastAPI:
    from ratchet.mocktools.app import create_app
    from ratchet.mocktools.world import World

    world_dir = settings.ratchet_scenarios_dir / "world"
    if world_dir.is_dir() and not settings.ratchet_public_mode:
        return create_app(settings, store, mode="record", world=World.load(world_dir))
    return create_app(settings, store, mode="replay")


def build_apps(
    settings: Settings, store: Store, orchestrator: Orchestrator
) -> list[tuple[str, FastAPI, str, int]]:
    """``(name, app, host, port)`` for the three HTTP services."""
    from ratchet.proxy.app import create_app as proxy_app

    return [
        (
            "dashboard",
            _dashboard(settings, store, orchestrator),
            settings.ratchet_dashboard_host,
            settings.ratchet_dashboard_port,
        ),
        (
            "proxy",
            proxy_app(settings=settings, store=store),
            "127.0.0.1",
            settings.ratchet_proxy_port,
        ),
        ("mock-tools", _mock(settings, store), "127.0.0.1", settings.ratchet_mock_tools_port),
    ]


def _server(name: str, app: FastAPI, host: str, port: int, settings: Settings) -> QuietServer:
    extra: dict[str, Any] = {}  # Any: uvicorn.Config keyword arguments of mixed types
    if name == "dashboard":
        extra = {"proxy_headers": True, "forwarded_allow_ips": settings.ratchet_forwarded_allow_ips}
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_config=None,
        log_level=settings.ratchet_log_level.lower(),
        access_log=False,
        timeout_graceful_shutdown=5,
        **extra,
    )
    return QuietServer(config)


async def serve(settings: Settings, *, watch: bool = True) -> None:
    from ratchet.orchestrator.replaying import sandbox_refusal

    refusal = sandbox_refusal(settings)
    if refusal is not None:  # every replay would fail sandbox_failed (ADR 0017)
        raise ConfigError(refusal, code="config_error", context={"field": "ratchet_sandbox"})
    public = public_mode_refusal(settings)
    if public is not None:  # the default secret would let anyone mint action tokens
        raise ConfigError(
            public, code="config_error", context={"field": "ratchet_action_token_secret"}
        )
    store = await asyncio.to_thread(Store.open, settings.ratchet_data_dir)
    orchestrator = Orchestrator(settings, store)
    servers = [_server(*spec, settings) for spec in build_apps(settings, store, orchestrator)]
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    await orchestrator.start(watch=watch)
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    waiter = asyncio.create_task(stop.wait())
    _log.info("serve.start", watch=watch, n_servers=len(servers))
    try:
        await asyncio.wait([*tasks, waiter], return_when=asyncio.FIRST_COMPLETED)
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)
        waiter.cancel()
        await orchestrator.stop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.remove_signal_handler(sig)
        _log.info("serve.stop")
