"""``GET /runs?unblessed=1``: recent agent runs for the Traces page "recent runs" view.

Two sources, newest activity first: finalized ``live`` traces (``ratchet agent run``) and
runs an unmodified external harness left through the proxy (step files, no
``trace.json``; ``source="captured"``). Both can be blessed with ``POST /runs/{id}/bless``
or ``ratchet bless <run_id>``; a captured run is finalized into a ``live`` trace first.
``unblessed=0`` adds the blessed goldens. Operator host only (403 ``not_public``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from ratchet.capture.external import captured_runs
from ratchet.copy import ERROR_COPY
from ratchet.dashboard_api.deps import ServicesDep
from ratchet.errors import ForbiddenError
from ratchet.store import Store
from ratchet.util import preview

__all__ = ["router", "RunView", "RunsView"]

router = APIRouter(tags=["traces"])
_PREVIEW = 200


class RunView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    source: Literal["captured", "live", "golden"]  # captured = proxy steps, no trace.json yet
    task_preview: str  # first 200 characters of the task
    task_label: str
    agent_tier: str
    n_steps: int
    last_activity_at: datetime
    blessed: bool


class RunsView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunView]


def _runs(store: Store, unblessed: bool, limit: int) -> RunsView:
    items = [
        RunView(
            run_id=run.run_id,
            source="captured",
            task_preview=run.task_preview,
            task_label="",
            agent_tier=run.agent_tier,
            n_steps=run.n_steps,
            last_activity_at=run.last_step_at,
            blessed=False,
        )
        for run in captured_runs(store, limit=limit)
    ]
    kinds = ("live",) if unblessed else ("live", "golden")
    for kind in kinds:
        for row in store.list_traces(kind=kind, limit=limit):
            if not store.has_trace(row.run_id):
                continue
            trace = store.load_trace(row.run_id)
            items.append(
                RunView(
                    run_id=trace.run_id,
                    source="golden" if trace.kind == "golden" else "live",
                    task_preview=preview(trace.task_input, _PREVIEW),
                    task_label=trace.task_label,
                    agent_tier=trace.agent_tier,
                    n_steps=len(trace.steps),
                    last_activity_at=trace.created_at,
                    blessed=trace.kind == "golden",
                )
            )
    items.sort(key=lambda r: (r.last_activity_at, r.run_id), reverse=True)
    return RunsView(items=items[:limit])


@router.get("/runs", response_model=RunsView)
async def list_runs(
    services: ServicesDep,
    unblessed: bool = True,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RunsView:
    """Recent runs, newest first; ``unblessed=1`` (default) leaves out the goldens."""
    if services.settings.ratchet_public_mode:
        raise ForbiddenError(ERROR_COPY["not_public"], code="not_public")
    return await asyncio.to_thread(_runs, services.store, unblessed, limit)
