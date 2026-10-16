"""Golden library and trace actions (CONTRACTS §7).

On the public host the library is hidden (403 ``not_public``), bless/unbless/criteria are
refused, and ``GET /traces/{run_id}`` serves only traces cited by a verdict the caller can
list, so pair evidence links keep working.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from ratchet.copy import ERROR_COPY
from ratchet.dashboard_api.builders import check_id, trace_summary, visible_trace_ids
from ratchet.dashboard_api.deps import ServicesDep, SessionDep, VisibleTraceCache, action_guard
from ratchet.dashboard_api.queries import MAX_LIMIT, trace_rows
from ratchet.dashboard_api.views import CriteriaIn, Page, TraceSummary
from ratchet.errors import ForbiddenError
from ratchet.models import Trace
from ratchet.store import Store

__all__ = ["router"]

router = APIRouter(tags=["traces"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
_NOT_PUBLIC = ERROR_COPY["not_public"]


def _summaries(
    store: Store,
    kind: str,
    skill: str | None,
    search: str | None,
    cursor: str | None,
    limit: int,
) -> Page[TraceSummary]:
    page = trace_rows(store, kind=kind, skill=skill, search=search, cursor=cursor, limit=limit)
    items: list[TraceSummary] = []
    for row in page.rows:
        run_id = str(row["run_id"])
        if store.has_trace(run_id):
            items.append(trace_summary(store.load_trace(run_id), row["last_replayed_at"]))
    return Page[TraceSummary](items=items, next_cursor=page.next_cursor)


@router.get("/traces", response_model=Page[TraceSummary])
async def list_traces(
    services: ServicesDep,
    kind: Literal["golden", "live", "replay"] = "golden",
    skill: Annotated[str | None, Query(max_length=64)] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    cursor: str | None = None,
    limit: Limit = 50,
) -> Page[TraceSummary]:
    if services.settings.ratchet_public_mode:
        raise ForbiddenError(_NOT_PUBLIC, code="not_public")
    return await asyncio.to_thread(_summaries, services.store, kind, skill, search, cursor, limit)


def _load_visible(
    store: Store, run_id: str, cache: VisibleTraceCache | None, session_hash: str | None
) -> Trace:
    """``cache`` is set on the public host only: the trace must be cited by a verdict the
    session can list (the cited-id set is cached per session for a few seconds)."""
    check_id(run_id, "trace")
    if cache is not None:
        load = lambda: visible_trace_ids(store, session_hash)  # noqa: E731
        if not cache.contains(session_hash, run_id, load):
            raise ForbiddenError(_NOT_PUBLIC, code="not_public")
    return store.load_trace(run_id)


@router.get("/traces/{run_id}", response_model=Trace)
async def get_trace(run_id: str, services: ServicesDep, session: SessionDep) -> Trace:
    cache = services.visible_traces if services.settings.ratchet_public_mode else None
    return await asyncio.to_thread(_load_visible, services.store, run_id, cache, session.hash)


@router.post(
    "/runs/{run_id}/bless",
    response_model=Trace,
    dependencies=[Depends(action_guard(public_allowed=False))],
)
async def bless(run_id: str, services: ServicesDep) -> Trace:
    """Mark a live run golden (PII pass, Nano enrichment). Refused on the public host."""
    check_id(run_id, "run")
    return await services.backend.bless(services.store, services.settings, run_id)


@router.post(
    "/traces/{run_id}/unbless",
    response_model=Trace,
    dependencies=[Depends(action_guard(public_allowed=False))],
)
async def unbless(run_id: str, services: ServicesDep) -> Trace:
    check_id(run_id, "trace")
    return await asyncio.to_thread(
        services.backend.unbless, services.store, services.settings, run_id
    )


@router.put(
    "/traces/{run_id}/criteria",
    response_model=Trace,
    dependencies=[Depends(action_guard(public_allowed=False))],
)
async def put_criteria(run_id: str, body: CriteriaIn, services: ServicesDep) -> Trace:
    """Replace the golden's success criteria."""
    check_id(run_id, "trace")
    criteria = [c.strip() for c in body.criteria if c.strip()]
    return await asyncio.to_thread(
        services.backend.set_criteria, services.store, services.settings, run_id, criteria
    )
