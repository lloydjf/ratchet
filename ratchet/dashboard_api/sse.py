"""Server-Sent Events for ``GET /api/v1/events/stream``.

Frames are ``id: <event id>``, ``event: <kind>``, ``data: <Event row JSON>``. A heartbeat
comment goes out every 15 s (sse-starlette's ping). The stream resumes after
``max(since, Last-Event-ID)``, or from the newest event when neither is given. New rows
come from one shared poller per app (``hub.EventHub``, a worker-thread read every 250 ms
fanned out to every open stream), and the stream stops when the client disconnects
(sse-starlette cancels the generator).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass

from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.background import BackgroundTask

from ratchet.dashboard_api.hub import EventHub, max_event_id
from ratchet.log import get_logger
from ratchet.models import Event
from ratchet.store import Store

__all__ = ["StreamConfig", "resume_point", "event_frames", "stream_response", "max_event_id"]

_log = get_logger("dashboard_api")


@dataclass(frozen=True)
class StreamConfig:
    poll_s: float = 0.25
    heartbeat_s: float = 15.0
    batch: int = 200
    max_duration_s: float | None = None  # tests only: end the stream after this long


def resume_point(since: int | None, last_event_id: str | None) -> int | None:
    """The id to resume after; ``None`` means "from now on"."""
    candidates: list[int] = []
    if since is not None and since >= 0:
        candidates.append(since)
    if last_event_id is not None and last_event_id.strip().isdigit():
        candidates.append(int(last_event_id.strip()))
    return max(candidates) if candidates else None


async def event_frames(
    store: Store,
    *,
    session_hash: str | None,
    after: int | None,
    kinds: Sequence[str],
    config: StreamConfig,
    hub: EventHub | None = None,
    on_close: Callable[[], None] | None = None,
) -> AsyncIterator[ServerSentEvent]:
    """Yield new events for this session (plus global ones) until cancelled.

    Events come from the app's shared :class:`EventHub` poller (a private one when no hub
    is given); a resume point older than "now" is backfilled first."""
    hub = hub or EventHub(store, poll_s=config.poll_s, batch=config.batch)
    started = time.monotonic()
    sent = 0
    sub = None
    try:
        sub = await hub.subscribe(session_hash, kinds)
        if after is not None:
            for event in await hub.backfill(sub, after):
                sent += 1
                yield _frame(event)
        while not sub.dropped:
            limit = config.max_duration_s
            if limit is not None and time.monotonic() - started >= limit:
                return
            try:
                event = await asyncio.wait_for(sub.queue.get(), timeout=config.poll_s)
            except TimeoutError:
                continue
            sent += 1
            yield _frame(event)
    finally:
        if sub is not None:
            hub.unsubscribe(sub)
        if on_close is not None:
            on_close()
        _log.info("api.sse.closed", sent=sent)


def _frame(event: Event) -> ServerSentEvent:
    return ServerSentEvent(data=event.model_dump_json(), event=event.kind, id=str(event.id))


def stream_response(
    frames: AsyncIterator[ServerSentEvent],
    config: StreamConfig,
    *,
    on_close: Callable[[], None] | None = None,
) -> EventSourceResponse:
    """``on_close`` also runs as the response's background task, so a stream whose
    generator never started still frees its slot (it must be idempotent)."""
    return EventSourceResponse(
        frames,
        ping=config.heartbeat_s,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(on_close) if on_close is not None else None,
    )
