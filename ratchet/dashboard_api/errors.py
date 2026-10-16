"""Error envelope.

Every error leaves the API as ``{"error": {"code", "message", "request_id", "details"?}}``
with the ``RatchetError.http_status``. Validation errors become 422 ``bad_request``; unknown
routes under ``/api`` become 404 ``not_found``. Input values are never echoed back.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from ratchet.copy_errors import NOT_READY_HOST
from ratchet.errors import RatchetError
from ratchet.log import get_logger, request_id_var

__all__ = [
    "BadRequest",
    "RateLimited",
    "NotReady",
    "error_response",
    "install_error_handlers",
]

_log = get_logger("dashboard_api")


class BadRequest(RatchetError):
    """The caller sent something the API cannot use (bad cursor, bad filter, bad body)."""

    code = "bad_request"
    http_status = 400


class RateLimited(RatchetError):
    """10 actions per minute per client IP (``rate_limited``)."""

    code = "rate_limited"
    http_status = 429

    def __init__(self, retry_after_s: float) -> None:
        super().__init__("Too many actions in a minute. Wait a moment and try again.")
        self.retry_after_s = max(1, int(retry_after_s + 0.999))


class NotReady(RatchetError):
    """A component the route needs is not wired (no orchestrator, module not installed).

    The message is always :data:`ratchet.copy.NOT_READY_HOST` (the 409 ``not_ready`` action
    refusal has its own text); the caller's specific reason goes to ``details.reason``."""

    code = "not_ready"
    http_status = 503

    def __init__(self, reason: str | None = None, *, context: dict[str, object] | None = None):
        details = dict(context or {})
        if reason:
            details["reason"] = reason
        super().__init__(NOT_READY_HOST, context=details)


def error_response(
    status: int,
    code: str,
    message: str,
    *,
    details: object | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {
        "code": code,
        "message": message,
        "request_id": request_id_var.get(),
    }
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status, headers=dict(headers or {}))


async def _ratchet_error(request: Request, exc: Exception) -> Response:
    if not isinstance(exc, RatchetError):  # pragma: no cover - registered for RatchetError
        raise exc
    log = _log.warning if exc.http_status < 500 else _log.error
    log("api.request.refused", code=exc.code, status=exc.http_status)
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimited):
        headers["Retry-After"] = str(exc.retry_after_s)
    details = {k: v for k, v in exc.context.items() if isinstance(v, str | int | float | bool)}
    return error_response(
        exc.http_status, exc.code, exc.message, details=details or None, headers=headers
    )


async def _validation_error(request: Request, exc: Exception) -> Response:
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    # Locations and messages only; never the offending input (it may hold content).
    details = [
        {"loc": [str(part) for part in err.get("loc", ())], "msg": str(err.get("msg", ""))}
        for err in errors
    ]
    return error_response(422, "bad_request", "The request is not valid.", details=details)


async def _http_error(request: Request, exc: Exception) -> Response:
    status = exc.status_code if isinstance(exc, StarletteHTTPException) else 500
    if status == 404:
        return error_response(404, "not_found", "No such route.")
    if status == 405:
        return error_response(405, "method_not_allowed", "Method not allowed on this route.")
    detail = exc.detail if isinstance(exc, StarletteHTTPException) else "error"
    return error_response(status, f"http_{status}", str(detail))


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RatchetError, _ratchet_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
