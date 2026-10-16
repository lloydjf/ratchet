"""Verdicts, pair detail and the verdict actions (CONTRACTS §7).

Lists show the caller's session plus global verdicts. A verdict id is an unguessable,
shareable deep link (deep links are stable and shareable), so detail and pair
routes open any verdict. On the public host, actions are allowed only on verdicts of the
caller's own Simulate session.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ratchet import copy
from ratchet.dashboard_api.builders import (
    load_verdict_or_none,
    pair_view,
    require_own_pair,
    require_own_verdict,
    verdict_summary,
    verdict_view,
)
from ratchet.dashboard_api.deps import ServicesDep, SessionDep, action_guard
from ratchet.dashboard_api.queries import MAX_LIMIT, verdict_rows
from ratchet.dashboard_api.views import Page, PairView, ReblessView, VerdictSummary, VerdictView
from ratchet.models import Verdict
from ratchet.store import Store

__all__ = ["router", "actor_for"]

router = APIRouter(tags=["verdicts"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def actor_for(public_mode: bool) -> str:
    return "judge" if public_mode else "operator"


def _summaries(
    store: Store, session_hash: str | None, cursor: str | None, limit: int
) -> Page[VerdictSummary]:
    page = verdict_rows(store, session_hash, cursor, limit)
    items: list[VerdictSummary] = []
    for row in page.rows:
        verdict = load_verdict_or_none(store, str(row["verdict_id"]))
        if verdict is not None:
            items.append(verdict_summary(verdict))
    return Page[VerdictSummary](items=items, next_cursor=page.next_cursor)


@router.get("/verdicts", response_model=Page[VerdictSummary])
async def list_verdicts(
    services: ServicesDep, session: SessionDep, cursor: str | None = None, limit: Limit = 50
) -> Page[VerdictSummary]:
    return await asyncio.to_thread(_summaries, services.store, session.hash, cursor, limit)


@router.get("/verdicts/{verdict_id}", response_model=VerdictView)
async def get_verdict(verdict_id: str, services: ServicesDep) -> VerdictView:
    """While the edit runs: ``verdict=null`` and ``progress`` from the orchestrator."""
    return await asyncio.to_thread(verdict_view, services.store, services.backend, verdict_id)


@router.get("/verdicts/{verdict_id}/pairs/{golden}/{replay}", response_model=PairView)
async def get_pair(verdict_id: str, golden: str, replay: str, services: ServicesDep) -> PairView:
    return await asyncio.to_thread(pair_view, services.store, verdict_id, golden, replay)


async def _act(
    services: ServicesDep, session_hash: str | None, verdict_id: str, which: str
) -> VerdictView:
    store, settings = services.store, services.settings
    if settings.ratchet_public_mode:
        await asyncio.to_thread(require_own_verdict, store, verdict_id, session_hash)
    actor = actor_for(settings.ratchet_public_mode)
    before = await asyncio.to_thread(load_verdict_or_none, store, verdict_id)
    action = services.backend.rollback if which == "rollback" else services.backend.approve
    verdict: Verdict = await action(store, settings, verdict_id, actor=actor)
    view = await asyncio.to_thread(verdict_view, store, services.backend, verdict.verdict_id)
    toast = (
        copy.action_toast(which, before.action, before.affected_skills)
        if before is not None
        else None
    )
    return view.model_copy(update={"toast": toast})


@router.post(
    "/verdicts/{verdict_id}/rollback",
    response_model=VerdictView,
    dependencies=[Depends(action_guard())],
)
async def rollback(verdict_id: str, services: ServicesDep, session: SessionDep) -> VerdictView:
    """Revert the edit's commit on candidate; ``main`` is unchanged (409 ``action_locked``
    when another action on this verdict is in progress)."""
    return await _act(services, session.hash, verdict_id, "rollback")


@router.post(
    "/verdicts/{verdict_id}/approve",
    response_model=VerdictView,
    dependencies=[Depends(action_guard())],
)
async def approve(verdict_id: str, services: ServicesDep, session: SessionDep) -> VerdictView:
    """Merge candidate into ``main``."""
    return await _act(services, session.hash, verdict_id, "approve")


@router.post(
    "/pairs/{golden}/{replay}/rebless",
    response_model=ReblessView,
    dependencies=[Depends(action_guard())],
)
async def rebless(
    golden: str, replay: str, services: ServicesDep, session: SessionDep
) -> ReblessView:
    """The replay becomes the golden; the old golden is archived (reason ``drift``). On the
    public host this writes a session-scoped golden copy and never touches the library."""
    store, settings = services.store, services.settings
    session_hash: str | None = None
    if settings.ratchet_public_mode:
        await asyncio.to_thread(require_own_pair, store, golden, replay, session.hash)
        session_hash = session.hash
    new_golden, archived = await services.backend.rebless(
        store, settings, golden, replay, session_hash=session_hash
    )
    return ReblessView(
        new_golden_run_id=new_golden, archived_run_id=archived, toast=copy.REBLESS_TOAST
    )
