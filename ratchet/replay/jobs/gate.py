"""Submit gating for runners whose every job is a VM on a small instance quota.

``SubmitGate`` caps the jobs in flight: ``acquire()`` reserves a slot, waiting while the cap
is reached; ``settle(job_id)`` turns the reservation into a tracked job (or frees it when
the create failed); ``release(job_id)`` frees the slot when a poll sees a terminal state or
the job is cancelled. While full it asks ``refresh`` for the live set of unfinished jobs, so
a submit loop that only polls after every submit (and jobs left over from a restart) still
makes progress. ``Backoff`` is the capped exponential wait (15 s → 60 s) shared by the slot
wait and the quota-exhausted retry, bounded by one budget in seconds.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ratchet.errors import UpstreamError
from ratchet.log import get_logger
from ratchet.replay.jobs.s3 import Sleep

__all__ = ["Backoff", "SubmitGate", "Refresh", "BACKOFF_BASE_S", "BACKOFF_CAP_S"]

BACKOFF_BASE_S = 15.0
BACKOFF_CAP_S = 60.0

Refresh = Callable[[], Awaitable[set[str] | None]]

_log = get_logger("replay.jobs")


class Backoff:
    """``pause()`` sleeps 15, 30, 60, 60, … s (the last one trimmed to the budget) and
    returns False once ``budget_s`` has been spent."""

    def __init__(
        self,
        budget_s: float,
        sleep: Sleep,
        *,
        base_s: float = BACKOFF_BASE_S,
        cap_s: float = BACKOFF_CAP_S,
    ) -> None:
        self.budget_s = budget_s
        self.waited_s = 0.0
        self._sleep = sleep
        self._base, self._cap = base_s, cap_s
        self._attempt = 0

    async def pause(self) -> bool:
        remaining = self.budget_s - self.waited_s
        if remaining <= 0:
            return False
        delay = min(self._cap, self._base * 2**self._attempt, remaining)
        await self._sleep(delay)
        self.waited_s += delay
        self._attempt += 1
        return True


class SubmitGate:
    def __init__(self, max_in_flight: int, *, refresh: Refresh | None = None) -> None:
        self.max_in_flight = max(1, max_in_flight)
        self._refresh = refresh
        self._in_flight: set[str] = set()
        self._reserved = 0
        self._lock = asyncio.Lock()

    @property
    def in_flight(self) -> frozenset[str]:
        return frozenset(self._in_flight)

    def _take(self) -> bool:
        if len(self._in_flight) + self._reserved < self.max_in_flight:
            self._reserved += 1
            return True
        return False

    async def acquire(self, backoff: Backoff, *, batch_id: str) -> None:
        """Reserve a slot, or raise ``upstream_unavailable`` when the budget runs out."""
        while True:
            async with self._lock:
                if self._take():
                    return
            live = await self._refresh() if self._refresh is not None else None
            async with self._lock:
                if live is not None:
                    self._in_flight = set(live)
                if self._take():
                    return
                in_flight = len(self._in_flight)
            _log.warning(
                "jobs.submit_waiting",
                batch_id=batch_id,
                code="jobs_slots_full",
                in_flight=in_flight,
                max_in_flight=self.max_in_flight,
                waited_s=backoff.waited_s,
            )
            if not await backoff.pause():
                _log.warning("jobs.submit_gave_up", batch_id=batch_id, code="jobs_slots_full")
                raise UpstreamError(
                    "no Serverless Jobs slot became free in time",
                    code="upstream_unavailable",
                    context={"reason": "jobs_slots_full", "batch_id": batch_id},
                )

    def settle(self, job_id: str | None) -> None:
        """End a reservation: track ``job_id``, or just free the slot when it is None."""
        self._reserved = max(0, self._reserved - 1)
        if job_id:
            self._in_flight.add(job_id)

    def track(self, job_id: str) -> None:
        self._in_flight.add(job_id)

    def release(self, job_id: str) -> bool:
        """Free ``job_id``'s slot; True when it was being tracked."""
        if job_id in self._in_flight:
            self._in_flight.discard(job_id)
            return True
        return False
