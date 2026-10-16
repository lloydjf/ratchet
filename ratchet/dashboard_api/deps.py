"""Request dependencies: services, session, action guard (orchestrator-api-cli.md §8)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request, Response

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.copy import ERROR_COPY
from ratchet.dashboard_api.errors import NotReady, RateLimited
from ratchet.dashboard_api.hub import EventHub, StreamSlots
from ratchet.dashboard_api.metrics import ApiMetrics
from ratchet.dashboard_api.ports import Backend, OrchestratorLike, SimulateLike
from ratchet.dashboard_api.security import (
    ACTION_TOKEN_HEADER,
    SESSION_COOKIE,
    TokenBucket,
    check_action_token,
    new_session_cookie,
    session_hash_for,
    set_session_cookie,
    valid_session_cookie,
)
from ratchet.dashboard_api.sse import StreamConfig
from ratchet.errors import ForbiddenError
from ratchet.log import get_logger
from ratchet.store import Store

__all__ = [
    "ApiOptions",
    "Services",
    "Session",
    "get_services",
    "ServicesDep",
    "read_session",
    "SessionDep",
    "ensure_session",
    "NewSessionDep",
    "action_guard",
    "client_key",
    "Probe",
    "VisibleTraceCache",
]

_log = get_logger("dashboard_api")
Probe = Callable[[], Awaitable[bool]]


@dataclass
class ApiOptions:
    """Wiring that tests and ``ratchet serve`` may override; the defaults are production."""

    backend: Backend | None = None
    simulate: SimulateLike | None = None  # a prebuilt Simulate service (tests)
    stream: StreamConfig = field(default_factory=StreamConfig)
    limiter: TokenBucket | None = None
    demo_limiter: TokenBucket | None = None
    probes: Mapping[str, Probe] = field(default_factory=dict)  # extra /healthz components


class VisibleTraceCache:
    """Public host: per-session set of trace ids cited by the session's visible verdicts,
    so ``GET /traces/{id}`` does not reload every visible verdict on each request.

    A cached set is served for ``ttl_s``; a miss (a trace not in the set, e.g. a verdict
    that just landed) reloads it, but at most once per ``refresh_s`` per session, so random
    ids cannot force a reload per request. At most ``max_sessions`` sessions are kept."""

    def __init__(self, ttl_s: float = 30.0, refresh_s: float = 1.0, max_sessions: int = 512):
        self.ttl_s = ttl_s
        self.refresh_s = refresh_s
        self.max_sessions = max_sessions
        self._rows: dict[str | None, tuple[float, frozenset[str]]] = {}
        self.loads = 0

    def contains(
        self, session_hash: str | None, run_id: str, load: Callable[[], frozenset[str]]
    ) -> bool:
        now = time.monotonic()
        hit = self._rows.get(session_hash)
        if hit is not None:
            age = now - hit[0]
            if age < self.ttl_s and (run_id in hit[1] or age < self.refresh_s):
                return run_id in hit[1]
        value = load()
        self.loads += 1
        self._rows.pop(session_hash, None)
        while len(self._rows) >= self.max_sessions:
            self._rows.pop(next(iter(self._rows)))
        self._rows[session_hash] = (now, value)
        return run_id in value


class Services:
    """Everything a handler needs, held on ``app.state.services``."""

    def __init__(
        self,
        settings: Settings,
        store: Store | None,
        orchestrator: OrchestratorLike | None,
        options: ApiOptions,
    ) -> None:
        self.settings = settings
        self._store = store
        self.orchestrator = orchestrator
        self.options = options
        self.backend = options.backend or Backend()
        self.limiter = options.limiter or TokenBucket(10, 60.0)
        self.demo_limiter = options.demo_limiter or TokenBucket(60, 60.0)
        self._simulate = options.simulate
        self.last_demo_prune: float = 0.0
        self.metrics = ApiMetrics(lambda: self.store, self.up)
        self.stream_slots = StreamSlots(
            settings.ratchet_sse_max_per_client, settings.ratchet_sse_max_streams
        )
        self._hub: EventHub | None = None
        self.visible_traces = VisibleTraceCache()

    def hub(self) -> EventHub:
        """The one shared SSE poller for this app (built on first stream)."""
        if self._hub is None:
            config = self.options.stream
            self._hub = EventHub(self.store, poll_s=config.poll_s, batch=config.batch)
        return self._hub

    async def aclose(self) -> None:
        if self._hub is not None:
            await self._hub.aclose()

    @property
    def store(self) -> Store:
        """Opened on first use, so building the app (e.g. for OpenAPI export) touches no disk."""
        if self._store is None:
            self._store = Store.open(self.settings.ratchet_data_dir)
        return self._store

    @property
    def simulate_enabled(self) -> bool:
        return self.settings.ratchet_simulate_enabled

    def simulate(self) -> SimulateLike | None:
        """The Simulate service, built lazily; ``None`` when disabled or not wired."""
        if not self.simulate_enabled:
            return None
        if self._simulate is None and self.orchestrator is not None:
            try:
                self._simulate = self.backend.make_simulate(
                    self.settings, self.store, self.orchestrator
                )
            except NotReady:
                _log.warning("api.simulate.unavailable", reason="module_missing")
                return None
        return self._simulate

    def up(self) -> dict[str, bool]:
        return {
            "dashboard_api": True,
            "orchestrator": self.orchestrator is not None,
            "simulate": self.simulate_enabled and self._simulate is not None,
        }


async def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


@dataclass(frozen=True)
class Session:
    hash: str | None
    is_new: bool = False


async def read_session(request: Request) -> Session:
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie is None or not valid_session_cookie(cookie):
        return Session(None)
    return Session(session_hash_for(cookie))


SessionDep = Annotated[Session, Depends(read_session)]


async def ensure_session(request: Request, response: Response, services: ServicesDep) -> Session:
    """Like :func:`read_session`, but issues the cookie when there is none (``/meta``,
    ``/simulate``)."""
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie is not None and valid_session_cookie(cookie):
        return Session(session_hash_for(cookie))
    cookie = new_session_cookie()
    set_session_cookie(response, cookie, services.settings)
    return Session(session_hash_for(cookie), is_new=True)


NewSessionDep = Annotated[Session, Depends(ensure_session)]


def client_key(request: Request) -> str:
    """The rate-limit bucket key. Behind a reverse proxy, run uvicorn with proxy headers
    so ``request.client`` is the real client; never persisted or logged."""
    return request.client.host if request.client else "unknown"


def action_guard(*, public_allowed: bool = True) -> Callable[..., Awaitable[None]]:
    """Dependency for POST/PUT actions: ``not_public`` refusal, rate limit, action token."""

    async def guard(request: Request, services: ServicesDep) -> None:
        settings = services.settings
        if settings.ratchet_public_mode and not public_allowed:
            raise ForbiddenError(ERROR_COPY["not_public"], code="not_public")
        retry_after = services.limiter.take(client_key(request))
        if retry_after is not None:
            raise RateLimited(retry_after)
        if settings.ratchet_public_mode:
            presented = request.headers.get(ACTION_TOKEN_HEADER)
            check_action_token(settings, presented, clock.now())

    return guard
