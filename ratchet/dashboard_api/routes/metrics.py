"""``GET /metrics/product`` and ``GET /metrics/demo``.

The same rows ``ratchet metrics`` and ``ratchet metrics --demo`` print, as JSON
``{"since": <window start>, "rows": [{"metric", "value"}]}``. ``since`` is a window such as
``7d``, ``24h`` or ``30m`` (default ``7d``). Product metrics are aggregate counts and are
served on every host; the demo funnel is operator-only (403 ``not_public`` on the public
host). These are not the Prometheus ``/metrics`` at the root.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta

from fastapi import APIRouter

from ratchet import clock
from ratchet.copy import ERROR_COPY
from ratchet.dashboard_api.deps import ServicesDep
from ratchet.dashboard_api.errors import BadRequest
from ratchet.dashboard_api.views import MetricRow, MetricsView
from ratchet.errors import ForbiddenError
from ratchet.orchestrator.metrics import demo_funnel, product_metrics

__all__ = ["router", "parse_window"]

router = APIRouter(tags=["metrics"])
_WINDOW = re.compile(r"^(\d{1,4})([dhm])$")
_MAX_WINDOW = timedelta(days=366)


def parse_window(value: str) -> timedelta:
    """``7d`` / ``24h`` / ``30m`` → a timedelta (at most a year); 400 ``bad_request`` else."""
    match = _WINDOW.match(value.strip())
    if match is None:
        raise BadRequest("since must be a number and d, h or m, e.g. 7d.")
    amount, unit = int(match.group(1)), match.group(2)
    window = {"d": timedelta(days=amount), "h": timedelta(hours=amount)}.get(
        unit, timedelta(minutes=amount)
    )
    return min(window, _MAX_WINDOW)


def _view(since: datetime, rows: list[tuple[str, str]]) -> MetricsView:
    return MetricsView(since=since, rows=[MetricRow(metric=m, value=v) for m, v in rows])


@router.get("/metrics/product", response_model=MetricsView)
async def product(services: ServicesDep, since: str = "7d") -> MetricsView:
    start = clock.now() - parse_window(since)
    rows = await asyncio.to_thread(product_metrics, services.store, start)
    return _view(start, rows)


@router.get("/metrics/demo", response_model=MetricsView)
async def demo(services: ServicesDep, since: str = "7d") -> MetricsView:
    """The anonymous demo funnel. Operator host only (403 ``not_public``)."""
    if services.settings.ratchet_public_mode:
        raise ForbiddenError(ERROR_COPY["not_public"], code="not_public")
    start = clock.now() - parse_window(since)
    rows = await asyncio.to_thread(demo_funnel, services.store, start)
    return _view(start, rows)
