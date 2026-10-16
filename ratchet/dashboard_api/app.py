"""Dashboard + API app (port 8080; CONTRACTS §1, §7; orchestrator-api-cli.md §8).

Routes live under ``/api/v1`` with ``/api`` as an alias (ADR 0019; the alias is left out
of the OpenAPI schema). ``/healthz`` and ``/metrics`` are at the root. The built SPA is
served for every other path. The lifespan never starts the orchestrator (its caller owns
it); it only builds the Simulate service when Simulate is enabled.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from ratchet import __version__
from ratchet.config.settings import Settings, get_settings
from ratchet.dashboard_api.deps import ApiOptions, Services
from ratchet.dashboard_api.errors import install_error_handlers
from ratchet.dashboard_api.middleware import BodyLimitMiddleware, RequestContextMiddleware
from ratchet.dashboard_api.ports import OrchestratorLike
from ratchet.dashboard_api.routes import api_router
from ratchet.dashboard_api.routes.health import router as health_router
from ratchet.dashboard_api.static import install_static
from ratchet.dashboard_api.views import ErrorEnvelope
from ratchet.log import get_logger
from ratchet.store import Store

__all__ = ["create_app", "ApiOptions", "API_PREFIX", "API_ALIAS"]

API_PREFIX = "/api/v1"
API_ALIAS = "/api"
_log = get_logger("dashboard_api")
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "4XX": {"model": ErrorEnvelope, "description": "Error envelope"},
    "5XX": {"model": ErrorEnvelope, "description": "Error envelope"},
}


def create_app(
    settings: Settings | None = None,
    store: Store | None = None,
    orchestrator: OrchestratorLike | None = None,
    *,
    options: ApiOptions | None = None,
) -> FastAPI:
    """Build the app. ``store`` defaults to ``Store.open(settings.ratchet_data_dir)``,
    opened on first use; ``orchestrator`` is the caller's running ``Orchestrator``."""
    settings = settings or get_settings()
    services = Services(settings, store, orchestrator, options or ApiOptions())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await asyncio.to_thread(lambda: services.store)  # open + migrate off the loop
        simulate = services.simulate() is not None
        _log.info(
            "api.start",
            public_mode=settings.ratchet_public_mode,
            simulate=simulate,
            orchestrator=orchestrator is not None,
        )
        yield
        await services.aclose()

    app = FastAPI(
        title="Ratchet dashboard API",
        version=__version__,
        description="Regression CI for self-improving agents. Shapes: docs/CONTRACTS.md §7.",
        lifespan=lifespan,
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )
    app.state.services = services
    install_error_handlers(app)
    router = api_router()
    app.include_router(router, prefix=API_PREFIX, responses=_ERROR_RESPONSES)
    app.include_router(router, prefix=API_ALIAS, include_in_schema=False)
    app.include_router(health_router)
    install_static(app, settings)
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.ratchet_api_max_body_bytes)
    app.add_middleware(RequestContextMiddleware, metrics=services.metrics)
    return app
