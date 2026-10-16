"""``GET /healthz`` (cheap readiness) and ``GET /metrics`` (Prometheus). Root paths."""

from __future__ import annotations

import asyncio
import sqlite3

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from ratchet.dashboard_api.deps import Services, ServicesDep
from ratchet.dashboard_api.metrics import CONTENT_TYPE
from ratchet.dashboard_api.views import HealthView

__all__ = ["router"]

router = APIRouter(tags=["ops"])


def _db_ok(services: Services) -> bool:
    try:
        services.store.query_one("SELECT 1")
    except (sqlite3.Error, OSError):
        return False
    return True


def _simulate_state(services: Services) -> str:
    if not services.simulate_enabled:
        return "disabled"
    if services.simulate() is None:
        return "unavailable"
    return services.settings.ratchet_simulate_mode


@router.get("/healthz", response_model=HealthView, responses={503: {"model": HealthView}})
async def healthz(services: ServicesDep) -> Response:
    """``ok`` follows the database; extra probes (e.g. the proxy, wired by ``ratchet serve``)
    are reported but only fail the check if they are down."""
    db_ok = await asyncio.to_thread(_db_ok, services)
    components = {"db": "ok" if db_ok else "down", "simulate": _simulate_state(services)}
    ok = db_ok
    for name, probe in services.options.probes.items():
        try:
            up = await asyncio.wait_for(probe(), timeout=1.0)
        except (TimeoutError, OSError):
            up = False
        components[name] = "ok" if up else "down"
        ok = ok and up
    view = HealthView(ok=ok, components=components)
    return JSONResponse(view.model_dump(), status_code=200 if ok else 503)


@router.get("/metrics", response_class=Response, responses={200: {"content": {CONTENT_TYPE: {}}}})
async def metrics(services: ServicesDep) -> Response:
    body = await asyncio.to_thread(services.metrics.render)
    return Response(content=body, media_type=CONTENT_TYPE)
