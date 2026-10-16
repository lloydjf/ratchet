"""One shared events poller for every SSE stream, plus stream admission caps.

Before, every open stream polled the events table on its own every 250 ms, so N tabs meant
N queries per tick. :class:`EventHub` runs ONE poller task per app (started by the first
subscriber, stopped when the last leaves) that reads each new batch of events once and fans
it out to the subscribers whose filter matches (own session or global rows; kinds).

A subscriber that resumes from an older id first gets a filtered backfill of
``(after, joined_at]`` read straight from the store, then the live fan-out after
``joined_at``; registration and the poller's hand-off both happen on the event loop with no
await in between, so nothing is lost or duplicated. A subscriber that falls more than
``queue_max`` events behind is dropped; its browser reconnects with ``Last-Event-ID``.

:class:`StreamSlots` caps concurrent streams per client IP and in total; over the cap the
route answers 429 ``rate_limited`` before any stream starts.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import dataclass, field

from ratchet.dashboard_api.errors import RateLimited
from ratchet.log import get_logger
from ratchet.models import Event
from ratchet.store import Store

__all__ = ["EventHub", "Subscriber", "StreamSlots", "TooManyStreams", "max_event_id"]

_log = get_logger("dashboard_api")


def max_event_id(store: Store) -> int:
    row = store.query_one("SELECT COALESCE(MAX(id), 0) AS m FROM events")
    return int(row["m"]) if row is not None else 0


class TooManyStreams(RateLimited):
    """Too many open live-update streams from this client (or on this host)."""

    def __init__(self) -> None:
        super().__init__(5.0)
        self.message = "Too many live-update connections. Close a tab and try again."
        self.args = (self.message,)


@dataclass(eq=False)
class Subscriber:
    session_hash: str | None
    kinds: frozenset[str]
    joined_at: int
    queue: asyncio.Queue[Event] = field(default_factory=asyncio.Queue)
    dropped: bool = False

    def wants(self, event: Event) -> bool:
        if self.kinds and event.kind not in self.kinds:
            return False
        if event.session_hash is None:
            return True
        return self.session_hash is not None and event.session_hash == self.session_hash


class EventHub:
    """The shared poller. Use :meth:`subscribe` / :meth:`unsubscribe` from the event loop."""

    def __init__(
        self, store: Store, *, poll_s: float = 0.25, batch: int = 200, queue_max: int = 1000
    ) -> None:
        self.store = store
        self.poll_s = poll_s
        self.batch = batch
        self.queue_max = queue_max
        self.polls = 0  # number of events-table reads by the poller (tests, metrics)
        self._subs: set[Subscriber] = set()
        self._last: int | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    async def subscribe(self, session_hash: str | None, kinds: Sequence[str]) -> Subscriber:
        if self._last is None:
            self._last = await asyncio.to_thread(max_event_id, self.store)
        # No await between reading _last and registering: the poller hands this subscriber
        # every event after joined_at, and the caller backfills up to joined_at itself.
        sub = Subscriber(session_hash, frozenset(kinds), self._last)
        self._subs.add(sub)
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """Synchronous on purpose: it runs in a generator ``finally`` under cancellation,
        where any further await would be cancelled again (anyio cancel scopes)."""
        self._subs.discard(sub)
        if not self._subs and self._task is not None:
            self._task.cancel()
            self._task = None
            self._last = None  # the next first subscriber starts "from now on" again

    async def aclose(self) -> None:
        """Stop the poller (app shutdown)."""
        self._subs.clear()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def backfill(self, sub: Subscriber, after: int) -> list[Event]:
        """Events in ``(after, sub.joined_at]`` that match the subscriber's filter."""
        out: list[Event] = []
        cursor = after
        while cursor < sub.joined_at:
            rows = await asyncio.to_thread(
                self.store.events,
                since=cursor,
                before=sub.joined_at + 1,
                kinds=sorted(sub.kinds) or None,
                session_hash=sub.session_hash,
                include_global=sub.session_hash is not None,
                limit=self.batch,
            )
            out.extend(rows)
            if len(rows) < self.batch:
                break
            cursor = rows[-1].id
        return out

    async def _run(self) -> None:
        while True:
            last = self._last or 0
            rows = await asyncio.to_thread(self.store.events, since=last, limit=self.batch)
            self.polls += 1
            for event in rows:
                self._fan_out(event)
                self._last = event.id
            if len(rows) < self.batch:
                await asyncio.sleep(self.poll_s)

    def _fan_out(self, event: Event) -> None:
        for sub in list(self._subs):
            if sub.dropped or event.id <= sub.joined_at or not sub.wants(event):
                continue
            if sub.queue.qsize() >= self.queue_max:
                sub.dropped = True  # too slow; it reconnects with Last-Event-ID
                _log.warning("api.sse.dropped", reason="slow_consumer")
                continue
            sub.queue.put_nowait(event)


class StreamSlots:
    """Concurrent-stream admission: at most ``per_client`` per IP and ``total`` overall."""

    def __init__(self, per_client: int, total: int) -> None:
        self.per_client = per_client
        self.total = total
        self._open: dict[str, int] = {}

    @property
    def open(self) -> int:
        return sum(self._open.values())

    def acquire(self, client: str) -> None:
        if self.open >= self.total or self._open.get(client, 0) >= self.per_client:
            raise TooManyStreams()
        self._open[client] = self._open.get(client, 0) + 1

    def release(self, client: str) -> None:
        left = self._open.get(client, 0) - 1
        if left > 0:
            self._open[client] = left
        else:
            self._open.pop(client, None)
