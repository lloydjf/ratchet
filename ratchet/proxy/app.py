"""FastAPI app for the capture proxy (port 8081; CONTRACTS §1, §3).

Routes: ``POST /v1/chat/completions``, ``POST /v1/r/{run_id}/chat/completions`` (path-form
run id), ``POST /v1/responses`` (feature flag), ``GET /healthz``,
``GET /runs/{run_id}/steps``, ``GET /metrics``.

As a replay-worker sidecar (``sidecar=SidecarPolicy(...)``) the caller is the sandboxed
harness: purpose and tier are forced, spend is checked against the dispatch-time
allowance, and ``/runs/{run_id}/steps`` needs the worker's ``X-Ratchet-Admin-Token``.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from ratchet.config.models import spec_for
from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ForbiddenError, NotFoundError, RatchetError
from ratchet.llm.client import HEADER
from ratchet.log import get_logger, request_id_var
from ratchet.models import TIERS
from ratchet.proxy.embeddings import handle_embeddings
from ratchet.proxy.headers import BadRequest, request_id_from, valid_run_id
from ratchet.proxy.metrics import CONTENT_TYPE, ProxyMetrics
from ratchet.proxy.pipeline import ProxyState, handle_call, secret_values
from ratchet.proxy.policy import Limiter, SidecarPolicy
from ratchet.proxy.upstream import Upstream
from ratchet.store import Store

__all__ = ["create_app", "require_admin", "model_list"]

_log = get_logger("proxy")


def _error_response(exc: RatchetError, request_id: str | None) -> JSONResponse:
    headers = {"X-Request-Id": request_id} if request_id else {}
    run_id = exc.context.get("run_id")
    if isinstance(run_id, str):
        headers[HEADER.RUN_ID] = run_id
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message, "request_id": request_id}},
        status_code=exc.http_status,
        headers=headers,
    )


def _install_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request_id_from(request.headers)
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(RatchetError)
    async def ratchet_error_handler(request: Request, exc: RatchetError) -> Response:
        request_id = getattr(request.state, "request_id", None)
        _log.warning(
            "proxy.request.refused",
            code=exc.code,
            status=exc.http_status,
            path=request.url.path,
        )
        return _error_response(exc, request_id)


def require_admin(sidecar: SidecarPolicy | None, presented: str | None) -> None:
    """Sidecar admin routes are for the worker only; the sandbox never gets the token."""
    if sidecar is None:
        return
    expected = sidecar.admin_token
    if not expected or not presented or not secrets.compare_digest(presented, expected):
        raise ForbiddenError("sidecar admin route", code="sidecar_token_invalid")


def model_list(settings: Settings) -> dict[str, object]:
    """OpenAI ``GET /models`` body: one entry per tier (sim ids under ``RATCHET_UPSTREAM=sim``)."""
    specs = [spec_for(tier, settings) for tier in TIERS]
    data = [
        {
            "id": spec.model_id,
            "object": "model",
            "created": 0,
            "owned_by": "ratchet",
            "context_length": spec.context_tokens,
        }
        for spec in specs
    ]
    return {"object": "list", "data": data}


def _install_routes(app: FastAPI, state: ProxyState) -> None:
    @app.get("/v1/models")
    async def models() -> dict[str, object]:
        return model_list(state.settings)

    @app.get("/v1/r/{run_id}/models")
    async def models_for_run(run_id: str) -> dict[str, object]:
        return model_list(state.settings)

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        return await handle_call(state, request, api="chat")

    @app.post("/v1/r/{run_id}/chat/completions")
    async def chat_completions_for_run(run_id: str, request: Request) -> Response:
        return await handle_call(state, request, api="chat", path_run_id=run_id)

    async def responses(request: Request, run_id: str | None) -> Response:
        if not state.settings.ratchet_feature_responses_api:
            raise NotFoundError("the /v1/responses passthrough is disabled", code="not_found")
        return await handle_call(state, request, api="responses", path_run_id=run_id)

    @app.post("/v1/responses")
    async def responses_api(request: Request) -> Response:
        return await responses(request, None)

    @app.post("/v1/r/{run_id}/responses")
    async def responses_api_for_run(run_id: str, request: Request) -> Response:
        return await responses(request, run_id)

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> Response:
        return await handle_embeddings(state, request)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"ok": True, "upstream": state.upstream.label}

    @app.get("/runs/{run_id}/steps")
    async def run_steps(run_id: str, request: Request) -> dict[str, object]:
        require_admin(state.sidecar, request.headers.get(HEADER.ADMIN_TOKEN))
        if not valid_run_id(run_id):
            raise BadRequest("invalid run id")
        steps = await asyncio.to_thread(state.store.collect_steps, run_id)
        return {"run_id": run_id, "steps": [s.model_dump(mode="json") for s in steps]}

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=state.metrics.render(), media_type=CONTENT_TYPE)


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    upstream_transport: httpx.AsyncBaseTransport | None = None,
    *,
    retry_backoff_s: float = 2.0,
    sidecar: SidecarPolicy | None = None,
) -> FastAPI:
    """Build the proxy. ``retry_backoff_s`` is the base delay before the single retry;
    ``sidecar`` switches on replay-worker sidecar mode (see the module docstring)."""
    settings = settings or get_settings()
    store = store or Store.open(settings.ratchet_data_dir)
    upstream = Upstream(settings, upstream_transport, retry_backoff_s=retry_backoff_s)
    state = ProxyState(
        settings=settings,
        store=store,
        upstream=upstream,
        metrics=ProxyMetrics(),
        limiter=Limiter(),
        secrets=secret_values(settings),
        sidecar=sidecar,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        _log.info("proxy.start", upstream=upstream.label)
        try:
            yield
        finally:
            await upstream.aclose()

    app = FastAPI(title="ratchet-proxy", version="0.1.0", lifespan=lifespan)
    app.state.proxy = state
    _install_handlers(app)
    _install_routes(app, state)
    return app
