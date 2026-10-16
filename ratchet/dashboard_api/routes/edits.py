"""``GET /edits``, ``GET /edits/{id}``, ``GET /edits/{id}/logs``, ``POST /edits/{id}/replay``
(CONTRACTS §7).

``/logs`` (worker logs, visible from the verdict page's debug drawer) is the
operator's view of what ``ratchet logs --edit`` prints from disk: each batch's ``worker.log``
(the last :data:`MAX_LOG_LINES` lines over all batches) followed by the orchestrator's crash
records (``attempt_*/crash.json``) when there are any. Operator host only (403
``not_public``).
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from ratchet.copy import ERROR_COPY
from ratchet.dashboard_api.builders import check_id, edit_view, require_own_edit
from ratchet.dashboard_api.deps import ServicesDep, SessionDep, action_guard
from ratchet.dashboard_api.errors import NotReady
from ratchet.dashboard_api.ports import Backend
from ratchet.dashboard_api.queries import MAX_LIMIT, edit_rows
from ratchet.dashboard_api.views import EditView, LogsView, Page
from ratchet.errors import ForbiddenError, NotFoundError
from ratchet.store import Store

__all__ = ["router", "edit_logs", "MAX_LOG_LINES"]

router = APIRouter(tags=["edits"])
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
MAX_LOG_LINES = 500
_MAX_FRAMES = 40


def _tail(path: Path, n: int) -> list[str]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=n)]


def _crash_lines(path: Path) -> list[str]:
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [f"--- crash record {path.parent.name}: unreadable"]
    if not isinstance(info, dict):
        return [f"--- crash record {path.parent.name}: unreadable"]
    head = (
        f"--- crash in {info.get('state')} ({path.parent.name}): "
        f"{info.get('error_type')}: {info.get('message')}"
    )
    frames = info.get("frames")
    tail = [f"    {f}" for f in frames[-_MAX_FRAMES:]] if isinstance(frames, list) else []
    return [head, *tail]


def edit_logs(store: Store, edit_id: str, max_lines: int = MAX_LOG_LINES) -> LogsView:
    """Worker log tails (newest ``max_lines`` over all batches) plus crash records.
    404 when the edit is unknown."""
    store.load_edit(edit_id)  # NotFoundError → 404
    worker: list[str] = []
    for batch in store.batches_for_edit(edit_id):
        log = store.batch_dir(batch.batch_id) / "worker.log"
        if log.is_file():
            worker.append(f"--- worker log {batch.batch_id}")
            worker.extend(_tail(log, max_lines))
    truncated = len(worker) > max_lines
    lines = worker[-max_lines:]
    for crash in sorted(store.edit_dir(edit_id).glob("attempt_*/crash.json")):
        lines.extend(_crash_lines(crash))
    return LogsView(lines=lines, truncated=truncated)


def _edits(
    store: Store, backend: Backend, session_hash: str | None, cursor: str | None, limit: int
) -> Page[EditView]:
    page = edit_rows(store, session_hash, cursor, limit)
    items: list[EditView] = []
    for row in page.rows:
        try:
            items.append(edit_view(store, backend, str(row["edit_id"])))
        except NotFoundError:
            continue  # index row without its edit.json (purged); skip it
    return Page[EditView](items=items, next_cursor=page.next_cursor)


@router.get("/edits", response_model=Page[EditView])
async def list_edits(
    services: ServicesDep, session: SessionDep, cursor: str | None = None, limit: Limit = 50
) -> Page[EditView]:
    return await asyncio.to_thread(
        _edits, services.store, services.backend, session.hash, cursor, limit
    )


@router.get("/edits/{edit_id}", response_model=EditView)
async def get_edit(edit_id: str, services: ServicesDep) -> EditView:
    return await asyncio.to_thread(edit_view, services.store, services.backend, edit_id)


@router.get("/edits/{edit_id}/logs", response_model=LogsView)
async def get_edit_logs(edit_id: str, services: ServicesDep) -> LogsView:
    """The debug drawer's real logs. Operator host only (403 ``not_public``)."""
    if services.settings.ratchet_public_mode:
        raise ForbiddenError(ERROR_COPY["not_public"], code="not_public")
    check_id(edit_id, "edit")
    return await asyncio.to_thread(edit_logs, services.store, edit_id)


@router.post(
    "/edits/{edit_id}/replay",
    response_model=EditView,
    dependencies=[Depends(action_guard())],
)
async def replay_edit(edit_id: str, services: ServicesDep, session: SessionDep) -> EditView:
    """Force a replay: the orchestrator resets the edit to ``selecting``."""
    store = services.store
    if services.settings.ratchet_public_mode:
        await asyncio.to_thread(require_own_edit, store, edit_id, session.hash)
    else:
        await asyncio.to_thread(edit_view, store, services.backend, edit_id)  # 404 if unknown
    if services.orchestrator is None:
        raise NotReady("The orchestrator is not running on this host.")
    await services.orchestrator.replay(edit_id)
    return await asyncio.to_thread(edit_view, store, services.backend, edit_id)
