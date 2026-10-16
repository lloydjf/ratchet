"""The per-edit state machine, one async handler per state.

``detected → selecting → dispatching → collecting → judging → drifting → controlling →
deciding → acting → notifying → done``, plus the terminal ``failed_infra``. ``controlling``
(ADR 0023) replays contested pairs once more with the parent skill. The loop reads the
persisted state, runs that state's handler (idempotent: it reads its inputs from the store
and reuses saved artifacts), then persists the transition (``journal.transition``). A
process that dies at any point resumes from ``edits.state`` and reaches the same verdict.

An unexpected exception in a handler is logged with its type and routes the edit to
``failed_infra`` (WARN, held): an unknown failure never passes an edit.
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable

from ratchet.errors import RatchetError
from ratchet.log import bind, get_logger
from ratchet.orchestrator.context import EditContext, Services, build_context
from ratchet.orchestrator.controls import handle_controlling
from ratchet.orchestrator.deciding import (
    handle_acting,
    handle_deciding,
    handle_failed_infra,
    handle_notifying,
    refresh_stages,
)
from ratchet.orchestrator.journal import TERMINAL, transition
from ratchet.orchestrator.judging import handle_drifting, handle_judging
from ratchet.orchestrator.replaying import handle_dispatching, handle_selecting
from ratchet.orchestrator.retrying import handle_collecting
from ratchet.store.base import write_json_atomic

__all__ = ["HANDLERS", "Handler", "run_edit", "advance"]

Handler = Callable[[EditContext], Awaitable[str]]
HANDLERS: dict[str, Handler] = {
    "selecting": handle_selecting,
    "dispatching": handle_dispatching,
    "collecting": handle_collecting,
    "judging": handle_judging,
    "controlling": handle_controlling,
    "drifting": handle_drifting,
    "deciding": handle_deciding,
    "acting": handle_acting,
    "notifying": handle_notifying,
}
_log = get_logger("orchestrator")


def record_crash(ctx: EditContext, state: str, exc: BaseException) -> None:
    """Keep what a crash looked like next to the attempt, so an operator can see it with
    ``ratchet logs --edit`` instead of a bare ``internal_error`` (live bench construction
    lost 84 edits this way before this existed). Exception text only; no trace content."""
    frames = traceback.extract_tb(exc.__traceback__)[-8:]
    payload = {
        "state": state,
        "error_type": type(exc).__name__,
        "message": str(exc)[:500],
        "frames": [f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno} {f.name}" for f in frames],
    }
    write_json_atomic(ctx.work / "crash.json", payload)


async def advance(ctx: EditContext, state: str) -> str:
    """Run one state's handler and persist the transition; returns the new state."""
    handler = HANDLERS[state]
    try:
        nxt = await handler(ctx)
    except RatchetError as exc:
        _log.error("orchestrator.handler_failed", edit_id=ctx.edit_id, state=state, code=exc.code)
        nxt = "failed_infra"
        await asyncio.to_thread(ctx.store.set_edit_state, ctx.edit_id, state, error_code=exc.code)
    except Exception as exc:  # noqa: BLE001 - routed to failed_infra, never passed
        _log.error(
            "orchestrator.handler_crashed",
            edit_id=ctx.edit_id,
            state=state,
            error_type=type(exc).__name__,
        )
        await asyncio.to_thread(record_crash, ctx, state, exc)
        nxt = "failed_infra"
        await asyncio.to_thread(
            ctx.store.set_edit_state, ctx.edit_id, state, error_code="internal_error"
        )
    await transition(ctx, state, nxt)
    return nxt


async def run_edit(services: Services, edit_id: str) -> str:
    """Drive one edit from its persisted state to a terminal state; returns that state."""
    ctx = await asyncio.to_thread(build_context, services, edit_id)
    with bind(edit_id=edit_id, session_hash=ctx.session_hash):
        state = (await asyncio.to_thread(ctx.store.edit_state, edit_id)).state
        if state == "detected":
            await transition(ctx, "detected", "selecting")
            state = "selecting"
        while state not in TERMINAL:
            state = await advance(ctx, state)
        if state == "failed_infra":
            await handle_failed_infra(ctx)
        else:
            await asyncio.to_thread(refresh_stages, ctx)
        _log.info("orchestrator.edit.done", edit_id=edit_id, state=state)
    return state
