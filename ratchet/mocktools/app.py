"""FastAPI app for the mock tool server (CONTRACTS §5).

Routes: ``POST /tools/{tool}`` (``{"args": {...}}`` + ``X-Ratchet-Run-Id``),
``POST /tools/{tool}/notify`` (local tool executions), ``GET /log``, ``GET|POST /mode``,
``GET /healthz``, ``GET /metrics`` and, when the mcp SDK is installed, ``POST|DELETE /mcp``
(``GET /mcp`` is 405: no server-initiated SSE stream).
Errors use the envelope ``{"error": {"code", "message", "request_id"}}``.

As a replay-worker sidecar the server faces the sandbox, so ``create_app`` takes
``admin_token`` (``/log`` and ``/mode`` then need ``X-Ratchet-Admin-Token``, which only the
worker holds) and ``run_id`` (tool calls and ``/notify`` must carry exactly that run id, so
the sandbox cannot write steps into another run). ``/notify`` already accepts only local
read tools, so it cannot forge a mocked tool's record.
"""

from __future__ import annotations

import asyncio
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.routing import Route

from ratchet import clock
from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ConfigError, ForbiddenError, RatchetError
from ratchet.log import get_logger, request_id_var
from ratchet.mocktools.engine import BadRequest, MockToolEngine, Mode, ToolResponse
from ratchet.mocktools.mcp_server import HeaderParser, build_mcp
from ratchet.mocktools.metrics import CONTENT_TYPE
from ratchet.mocktools.normalize import Tokenizer
from ratchet.mocktools.world import World
from ratchet.store import Store
from ratchet.tools.catalog import from_wire_name

__all__ = [
    "create_app",
    "default_tokenizer",
    "parse_call_headers",
    "PiiHook",
    "RUN_ID_HEADER",
    "ADMIN_TOKEN_HEADER",
    "pinned_parser",
    "require_admin",
]

RUN_ID_HEADER = "X-Ratchet-Run-Id"
ADMIN_TOKEN_HEADER = "X-Ratchet-Admin-Token"
FROZEN_TIME_HEADER = "X-Ratchet-Frozen-Time"
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_log = get_logger("mocktools")


def parse_call_headers(run_id: str | None, frozen: str | None) -> tuple[str, datetime | None]:
    """Validate the run id (required) and the optional frozen time header."""
    if not run_id:
        raise BadRequest(f"{RUN_ID_HEADER} header is required", code="missing_run_id")
    if not _RUN_ID.match(run_id) or ".." in run_id:
        raise BadRequest("run id has invalid characters", code="bad_run_id")
    if not frozen:
        return run_id, None
    try:
        return run_id, clock.parse(frozen.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequest(
            f"{FROZEN_TIME_HEADER} must be ISO 8601 with an offset", code="bad_frozen_time"
        ) from exc


class PiiHook:
    """Adapts ``ratchet.pii.PiiTokenizer`` to the engine: call to tokenize, flush to save."""

    def __init__(self, pii: PiiLike) -> None:
        self._pii = pii

    def __call__(self, value: object) -> object:
        return self._pii.tokenize_obj(value)

    def flush(self) -> None:
        self._pii.save()


class PiiLike(Protocol):
    def tokenize_obj(self, value: object) -> object: ...

    def save(self) -> None: ...


def default_tokenizer(settings: Settings, mode: Mode) -> Tokenizer | None:
    """``ratchet.pii.PiiTokenizer`` (imported lazily); record mode refuses to run without it."""
    try:
        from ratchet.pii import PiiTokenizer
    except ImportError as exc:
        if mode == "record":
            raise ConfigError(
                "record mode needs ratchet.pii so cassettes hold no raw PII", code="config_error"
            ) from exc
        _log.warning("mocktools.pii.unavailable")
        return None
    return PiiHook(PiiTokenizer.for_settings(settings))


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or f"req_{secrets.token_hex(6)}"


def _envelope(status: int, code: str, message: str) -> JSONResponse:
    rid = request_id_var.get()
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": rid}}, status_code=status
    )


def _respond(answer: ToolResponse) -> JSONResponse:
    body = dict(answer.body)
    error = body.get("error")
    if isinstance(error, dict):
        body["error"] = {**error, "request_id": request_id_var.get()}
    return JSONResponse(body, status_code=answer.status)


async def _json_object(request: Request) -> dict[str, object]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise BadRequest("request body must be JSON", code="bad_request") from exc
    if not isinstance(payload, dict):
        raise BadRequest("request body must be a JSON object", code="bad_request")
    return {str(k): v for k, v in payload.items()}


def _args(payload: dict[str, object]) -> dict[str, object]:
    args = payload.get("args", {})
    if not isinstance(args, dict):
        raise BadRequest('"args" must be a JSON object', code="bad_request")
    return {str(k): v for k, v in args.items()}


def pinned_parser(run_id: str | None) -> HeaderParser:
    """``parse_call_headers``, refusing any run id but ``run_id`` when one is pinned."""

    def parse(raw_run_id: str | None, frozen: str | None) -> tuple[str, datetime | None]:
        parsed = parse_call_headers(raw_run_id, frozen)
        if run_id is not None and parsed[0] != run_id:
            raise ForbiddenError("this tool server serves one run only", code="run_id_mismatch")
        return parsed

    return parse


def require_admin(expected: str | None, headers: Mapping[str, str]) -> None:
    """Admin routes are open on a dev/control-plane server and token-gated on a sidecar."""
    if expected is None:
        return
    presented = headers.get(ADMIN_TOKEN_HEADER.lower()) or headers.get(ADMIN_TOKEN_HEADER)
    if not presented or not secrets.compare_digest(presented, expected):
        raise ForbiddenError("sidecar admin route", code="sidecar_token_invalid")


def _register_routes(
    app: FastAPI,
    engine: MockToolEngine,
    mcp_enabled: bool,
    *,
    parse: HeaderParser = parse_call_headers,
    admin_token: str | None = None,
) -> None:
    def call_ids(request: Request) -> tuple[str, datetime | None]:
        return parse(request.headers.get(RUN_ID_HEADER), request.headers.get(FROZEN_TIME_HEADER))

    @app.post("/tools/{tool}")
    async def call_tool(tool: str, request: Request) -> JSONResponse:
        run_id, frozen = call_ids(request)
        args = _args(await _json_object(request))
        name = from_wire_name(tool) if "__" in tool else tool
        answer = await asyncio.to_thread(engine.call, name, args, run_id=run_id, frozen_time=frozen)
        return _respond(answer)

    @app.post("/tools/{tool}/notify")
    async def notify(tool: str, request: Request) -> JSONResponse:
        run_id, _ = call_ids(request)
        payload = await _json_object(request)
        name = from_wire_name(tool) if "__" in tool else tool
        step = await asyncio.to_thread(
            engine.notify, name, _args(payload), run_id=run_id, result=payload.get("result")
        )
        return JSONResponse({"ok": True, "step_id": step.step_id})

    @app.get("/log")
    async def log(request: Request, run_id: str | None = None) -> JSONResponse:
        require_admin(admin_token, request.headers)
        by_run = engine.unexpected(run_id)
        items = [call for calls in by_run.values() for call in calls]
        return JSONResponse(
            {
                "items": [c.model_dump(mode="json") for c in items],
                "by_run": {k: [c.model_dump(mode="json") for c in v] for k, v in by_run.items()},
            }
        )

    @app.get("/mode")
    async def get_mode(request: Request) -> dict[str, str]:
        require_admin(admin_token, request.headers)
        return {"mode": engine.mode}

    @app.post("/mode")
    async def set_mode(request: Request) -> dict[str, str]:
        require_admin(admin_token, request.headers)
        mode = (await _json_object(request)).get("mode")
        if mode not in ("record", "replay"):
            raise BadRequest('mode must be "record" or "replay"', code="bad_request")
        return {"mode": engine.set_mode("record" if mode == "record" else "replay")}

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        world = engine.world.name if engine.world is not None else None
        return {"ok": True, "mode": engine.mode, "world": world, "mcp": mcp_enabled}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(engine.metrics.render(), media_type=CONTENT_TYPE)


def _install_error_handling(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_ids(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = _request_id(request)
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response

    @app.exception_handler(RatchetError)
    async def ratchet_error(request: Request, exc: RatchetError) -> JSONResponse:
        return _envelope(exc.http_status, exc.code, exc.message)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # Runs outside the request-id middleware, so the id is re-read from the header.
        _log.error("mocktools.internal_error", error=type(exc).__name__)
        rid = request_id_var.get() or request.headers.get("x-request-id")
        error = {"code": "internal_error", "message": "mock tool error", "request_id": rid}
        body = {"error": error}
        return JSONResponse(body, status_code=500)


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    *,
    mode: Mode,
    cassette_root: Path | None = None,
    world: World | None = None,
    tokenizer: Tokenizer | None = None,
    overlay_root: Path | None = None,
    frozen_time: datetime | None = None,
    enable_mcp: bool = True,
    mcp_allowed_hosts: list[str] | None = None,
    admin_token: str | None = None,
    run_id: str | None = None,
) -> FastAPI:
    """Build the mock tool server. ``overlay_root`` is the world overlay (``world_ref``):
    a cassette root consulted before ``cassette_root`` for world-dependent tools.
    ``admin_token`` / ``run_id``: sidecar hardening (module docstring)."""
    settings = settings or get_settings()
    store = store or Store.open(settings.ratchet_data_dir)
    if mode == "record" and settings.ratchet_public_mode:
        raise ConfigError("record mode is disabled on the public host", code="not_public")
    engine = MockToolEngine(
        settings=settings,
        store=store,
        mode=mode,
        cassette_root=cassette_root or store.cassettes_dir,
        overlay_root=overlay_root,
        world=world,
        tokenizer=tokenizer if tokenizer is not None else default_tokenizer(settings, mode),
        frozen_time=frozen_time,
    )
    parse = pinned_parser(run_id)
    mount = build_mcp(engine, parse, allowed_hosts=mcp_allowed_hosts) if enable_mcp else None

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if mount is None:
            yield
            return
        async with mount.lifespan():
            yield

    app = FastAPI(title="ratchet-mock-tools", version="0.1.0", lifespan=lifespan)
    app.state.engine = engine
    _install_error_handling(app)
    _register_routes(app, engine, mount is not None, parse=parse, admin_token=admin_token)
    if mount is not None:
        # GET would open a server-initiated SSE stream that never ends; Hermes probes it before
        # connecting and stalled ~5.5 s per run on it. 405 = "no SSE stream".
        app.router.routes.append(Route("/mcp", endpoint=_no_sse_stream, methods=["GET"]))
        app.router.routes.append(Route("/mcp", endpoint=mount.asgi, methods=["POST", "DELETE"]))
    return app


async def _no_sse_stream(request: Request) -> Response:
    return Response(status_code=405, headers={"Allow": "POST, DELETE"})
