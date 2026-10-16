"""Request context: ``X-Request-Id`` echo/generation, one ``api.request.done`` log line,
request metrics, and a JSON 500 envelope for unexpected exceptions.

Pure ASGI (not ``BaseHTTPMiddleware``) so SSE streams pass through untouched. The log line
carries the route *template* (``/api/v1/verdicts/{verdict_id}``), method, status and
latency: no query string, no body, no client address.
"""

from __future__ import annotations

import re
import time

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ratchet.dashboard_api.errors import error_response
from ratchet.dashboard_api.metrics import ApiMetrics
from ratchet.log import get_logger, request_id_var
from ratchet.util import new_id

__all__ = ["RequestContextMiddleware", "BodyLimitMiddleware", "request_id_from"]

_log = get_logger("dashboard_api")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PREFIXES = ("/api/v1", "/api")  # longest first


def request_id_from(raw: str | None) -> str:
    return raw if raw is not None and _REQUEST_ID.fullmatch(raw) else new_id("req")


def _route_template(scope: Scope) -> str:
    """The matched path template with its mount prefix (FastAPI keeps the router-relative
    path on ``scope["route"]``)."""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if not isinstance(path, str):
        return "unmatched"
    actual = str(scope.get("path", ""))
    for prefix in _PREFIXES:
        if actual.startswith(prefix + "/") and not path.startswith(prefix + "/"):
            return prefix + path
    return path


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, metrics: ApiMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = None
        for name, value in scope.get("headers", []):
            if name == b"x-request-id":
                raw = value.decode("latin-1")
        request_id = request_id_from(raw)
        token = request_id_var.set(request_id)
        started = time.monotonic()
        state = {"status": 500, "started": False}

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = int(message["status"])
                state["started"] = True
                MutableHeaders(scope=message)["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception as exc:  # the last line of defense: never leak a traceback
            _log.error("api.request.failed", error=type(exc).__name__, route=_route_template(scope))
            if not state["started"]:
                response = error_response(
                    500, "internal_error", "Something went wrong on our side."
                )
                await response(scope, receive, send_with_id)
        finally:
            route = _route_template(scope)
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            status = int(state["status"])
            _log.info(
                "api.request.done",
                route=route,
                method=str(scope.get("method", "")),
                status=status,
                latency_ms=latency_ms,
            )
            self.metrics.observe(
                route=route,
                method=str(scope.get("method", "")),
                status=status,
                latency_ms=latency_ms,
            )
            request_id_var.reset(token)


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_TOO_LARGE = "The request body is too large."


class BodyLimitMiddleware:
    """413 ``payload_too_large`` for POST/PUT/PATCH bodies over ``max_bytes``.

    A declared ``Content-Length`` over the limit is refused before the app runs; otherwise
    the body (small by construction) is read up to the limit first and replayed to the app,
    so a chunked body over the limit is refused too, before any handler sees it."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers", []):
            if name == b"content-length" and (not value.isdigit() or int(value) > self.max_bytes):
                await error_response(413, "payload_too_large", _TOO_LARGE)(scope, receive, send)
                return
        chunks: list[bytes] = []
        seen = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":  # client went away
                return
            chunk = message.get("body", b"")
            seen += len(chunk)
            if seen > self.max_bytes:
                await error_response(413, "payload_too_large", _TOO_LARGE)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
