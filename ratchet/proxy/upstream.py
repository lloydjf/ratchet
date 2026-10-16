"""The single upstream connection pool: Token Factory, or the offline simulator (ADR 0018).

Retries: one retry on 5xx or timeout/transport failure, after ~2 s with jitter;
4xx is returned to the caller unchanged; a second failure becomes 502
``upstream_unavailable``. The API key is read here and only here, and is never logged.
"""

from __future__ import annotations

import asyncio
import importlib
import random
from collections.abc import Awaitable, Callable

import httpx

from ratchet.config.settings import Settings
from ratchet.errors import UpstreamError
from ratchet.log import get_logger

__all__ = ["Upstream", "SIM_BASE_URL"]

SIM_BASE_URL = "http://ratchet-sim/v1/"
_CONNECT_TIMEOUT_S = 10.0
_log = get_logger("proxy")


def _sim_transport() -> httpx.AsyncBaseTransport:
    # Imported lazily: the simulator is optional and must not load with the proxy module.
    module = importlib.import_module("ratchet.sim.app")
    return httpx.ASGITransport(app=module.create_app())


class Upstream:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        retry_backoff_s: float = 2.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.simulator = settings.ratchet_upstream == "sim"
        self.base_url = SIM_BASE_URL if self.simulator else settings.nebius_tf_base_url
        self.label = "sim" if self.simulator else settings.nebius_tf_base_url
        self._transport = transport
        self._retry_backoff_s = retry_backoff_s
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None

    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            transport = self._transport
            if transport is None and self.simulator:
                transport = _sim_transport()
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                transport=transport,
                limits=httpx.Limits(max_connections=64, max_keepalive_connections=32),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def api_key(self) -> str | None:
        """The Token Factory key for live upstreams (``config_error`` if unset)."""
        if self.simulator:
            return None
        return self.settings.require_live_key().get_secret_value()

    async def _backoff(self) -> None:
        delay = self._retry_backoff_s * random.uniform(0.75, 1.25)
        if delay > 0:
            await self._sleep(delay)

    async def _attempt(
        self,
        path: str,
        body: dict[str, object],
        headers: dict[str, str],
        timeout_s: float,
        *,
        stream: bool,
    ) -> httpx.Response:
        client = self.client()
        request = client.build_request(
            "POST",
            path,
            json=body,
            headers=headers,
            timeout=httpx.Timeout(timeout_s, connect=_CONNECT_TIMEOUT_S),
        )
        return await client.send(request, stream=stream)

    async def post(
        self,
        path: str,
        body: dict[str, object],
        headers: dict[str, str],
        *,
        timeout_s: float,
        stream: bool = False,
    ) -> httpx.Response:
        """POST with one retry. Returns any < 500 response (stream left open if ``stream``)."""
        failure = ""
        status: int | None = None
        for attempt in (1, 2):
            try:
                response = await self._attempt(path, body, headers, timeout_s, stream=stream)
            except httpx.TransportError as exc:
                failure, status = type(exc).__name__, None
            else:
                if response.status_code < 500:
                    return response
                await response.aclose()
                failure, status = "server_error", response.status_code
            _log.warning("proxy.upstream.failed", attempt=attempt, failure=failure, status=status)
            if attempt == 1:
                await self._backoff()
        raise UpstreamError(
            "model upstream failed after retry",
            code="upstream_unavailable",
            context={"failure": failure, "status": status},
        )
