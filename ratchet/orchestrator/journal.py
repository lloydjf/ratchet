"""Events, state transitions and the stage timeline for one edit.

Every pipeline event carries the edit's ``session_hash`` and ``edit_id`` + ``verdict_id``
in its payload; ``ref`` is the edit id except for ``verdict.*`` (the verdict id). A
transition writes ``edits.state`` *before* the next handler runs, emits
``orchestrator.state {from, to, edit_id, elapsed_ms}`` (pipeline state names) and records
how long the finished state took (``stages.json``, one entry per state; a re-run state
replaces its entry). Elapsed time is measured from the persisted ``state_updated_at``, so
it survives a restart.

``stage_timeline`` turns those per-state timings into the verdict's ``stages`` under the
fixed stage keys (detected … summary, done): dispatching + collecting are ``replaying``,
controlling is ``controls`` (ADR 0023), acting + notifying are ``done``, and judging is
split into level0 / nano / super / ultra at the times the last ``pair.level0`` and the last
opinion of each tier were emitted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from ratchet import clock
from ratchet.log import get_logger
from ratchet.models import TIERS, Event, StageTiming
from ratchet.orchestrator.context import EditContext
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.orchestrator.progress import STATE_STAGE
from ratchet.store import Store
from ratchet.store.base import write_json_atomic

__all__ = [
    "emit",
    "transition",
    "load_state_timings",
    "stage_timeline",
    "STATES",
    "TERMINAL",
]

STATES: tuple[str, ...] = (
    "detected",
    "selecting",
    "dispatching",
    "collecting",
    "judging",
    "drifting",
    "controlling",
    "deciding",
    "acting",
    "notifying",
    "done",
    "failed_infra",
)
TERMINAL: frozenset[str] = frozenset({"done", "failed_infra"})
_TIMINGS = TypeAdapter(list[StageTiming])
_log = get_logger("orchestrator")


async def emit(
    ctx: EditContext, kind: str, payload: dict[str, object], *, ref: str | None = None
) -> int:
    body = {"edit_id": ctx.edit_id, "verdict_id": verdict_id_for(ctx.edit_id), **payload}
    return await asyncio.to_thread(
        ctx.store.emit, kind, ref or ctx.edit_id, body, session_hash=ctx.session_hash
    )


def load_state_timings(work: Path) -> list[StageTiming]:
    """Per-state timings (``stage`` is the pipeline state name)."""
    path = work / "stages.json"
    if not path.exists():
        return []
    return _TIMINGS.validate_json(path.read_text(encoding="utf-8"))


def _judging_split(timing: StageTiming, events: list[Event]) -> list[StageTiming]:
    start = timing.started_at
    end = start + timedelta(milliseconds=timing.elapsed_ms)
    window = [e for e in events if start <= e.ts <= end]
    marks: list[tuple[str, datetime]] = []
    level0 = [e.ts for e in window if e.kind == "pair.level0"]
    marks.append(("level0", max(level0) if level0 else start))
    for tier in TIERS:
        seen = [e.ts for e in window if e.kind == "pair.opinion" and e.payload.get("tier") == tier]
        if seen:
            marks.append((tier, max(seen)))
    marks[-1] = (marks[-1][0], end)
    out: list[StageTiming] = []
    cursor = start
    for key, until in marks:
        until = max(until, cursor)
        out.append(StageTiming(stage=key, started_at=cursor, elapsed_ms=_ms(until - cursor)))
        cursor = until
    return out


def _ms(delta: timedelta) -> int:
    return max(int(delta.total_seconds() * 1000), 0)


def stage_timeline(store: Store, edit_id: str, work: Path) -> list[StageTiming]:
    """The verdict's ``stages``: stage keys, in pipeline order, consecutive keys merged."""
    events = store.events(ref=edit_id, limit=1000)
    keyed: list[StageTiming] = []
    for timing in load_state_timings(work):
        parts = (
            _judging_split(timing, events)
            if timing.stage == "judging"
            else [timing.model_copy(update={"stage": STATE_STAGE.get(timing.stage, "done")})]
        )
        for part in parts:
            if keyed and keyed[-1].stage == part.stage:
                last = keyed[-1]
                keyed[-1] = last.model_copy(
                    update={"elapsed_ms": last.elapsed_ms + part.elapsed_ms}
                )
            else:
                keyed.append(part)
    return keyed


def _record_transition(ctx: EditContext, src: str, dst: str) -> int:
    store = ctx.store
    started = store.edit_state(ctx.edit_id).state_updated_at
    elapsed = _ms(clock.now() - started)
    store.set_edit_state(ctx.edit_id, dst)
    timings = [s for s in load_state_timings(ctx.work) if s.stage != src]
    timings.append(StageTiming(stage=src, started_at=started, elapsed_ms=elapsed))
    write_json_atomic(ctx.work / "stages.json", [s.model_dump(mode="json") for s in timings])
    payload: dict[str, object] = {
        "edit_id": ctx.edit_id,
        "verdict_id": verdict_id_for(ctx.edit_id),
        "from": src,
        "to": dst,
        "elapsed_ms": elapsed,
    }
    store.emit("orchestrator.state", ctx.edit_id, payload, session_hash=ctx.session_hash)
    return elapsed


async def transition(ctx: EditContext, src: str, dst: str) -> None:
    if dst not in STATES:
        raise ValueError(f"unknown state {dst!r}")
    elapsed = await asyncio.to_thread(_record_transition, ctx, src, dst)
    _log.info("orchestrator.state", edit_id=ctx.edit_id, src=src, dst=dst, elapsed_ms=elapsed)
