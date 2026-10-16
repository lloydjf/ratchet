"""Calendar adapters over ``calendar.json``."""

from __future__ import annotations

from datetime import datetime

from ratchet import clock
from ratchet.mocktools.adapters.base import Adapter, Args, Result, opt_str, req_str, str_list
from ratchet.mocktools.world import CallContext, ToolArgumentError, WorldState
from ratchet.mocktools.world_models import CalendarEvent, Interval

__all__ = ["ADAPTERS", "overlaps"]


def _time(value: str, name: str) -> datetime:
    try:
        return clock.parse(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ToolArgumentError(f"{name} must be an ISO time with an offset") from exc


def overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _busy_for(state: WorldState, email: str, start: datetime, end: datetime) -> list[Result]:
    blocks: list[Interval] = [
        Interval(start=event.start, end=event.end)
        for event in state.calendar.events
        if email.casefold() in (a.casefold() for a in event.attendees)
    ]
    blocks.extend(state.calendar.busy.get(email, []))
    found = [
        b for b in blocks if overlaps(_time(b.start, "start"), _time(b.end, "end"), start, end)
    ]
    found.sort(key=lambda b: (_time(b.start, "start"), _time(b.end, "end")))
    return [{"start": b.start, "end": b.end} for b in found]


def freebusy(state: WorldState, args: Args, ctx: CallContext) -> Result:
    start_raw, end_raw = req_str(args, "start"), req_str(args, "end")
    start, end = _time(start_raw, "start"), _time(end_raw, "end")
    if end <= start:
        raise ToolArgumentError("end must be after start")
    attendees = str_list(args, "attendees", required=True)
    busy = {email: _busy_for(state, email, start, end) for email in attendees}
    return {"start": start_raw, "end": end_raw, "busy": busy}


def create(state: WorldState, args: Args, ctx: CallContext) -> Result:
    start_raw, end_raw = req_str(args, "start"), req_str(args, "end")
    if _time(end_raw, "end") <= _time(start_raw, "start"):
        raise ToolArgumentError("end must be after start")
    event = CalendarEvent(
        event_id=f"evt_{len(state.calendar.events) + 1:04d}",
        title=req_str(args, "title"),
        start=start_raw,
        end=end_raw,
        attendees=str_list(args, "attendees"),
        calendar=opt_str(args, "calendar", "primary") or "primary",
    )
    state.calendar.events.append(event)
    return {"status": "created", **event.model_dump(mode="json")}


ADAPTERS: dict[str, Adapter] = {"calendar.freebusy": freebusy, "calendar.create": create}
