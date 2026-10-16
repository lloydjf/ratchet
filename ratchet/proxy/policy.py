"""Tier policy, spending caps and per-purpose concurrency.

Checks run before anything is forwarded: an ``agent`` call on a tier other than the run's
recorded ``agent_tier`` is refused (409 ``tier_mismatch``, keeps replays honest), and a
call is refused once the daily cap, its purpose's daily cap or the bench hard cap has been
reached (429 ``budget_exceeded``).

Inside a replay worker the proxy is a sidecar with a scratch store that starts empty, so
the configured caps would never trip there. The orchestrator therefore computes the
remaining allowance from the control-plane ledger when it dispatches a batch
(:func:`remaining_allowance`, carried in ``ReplayBatch.budget_allowance_usd``) and the
sidecar enforces ``allowance - scratch spend`` instead (:class:`SidecarPolicy`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from ratchet import clock
from ratchet.capture.runs import RunMeta
from ratchet.config.settings import Settings
from ratchet.errors import BudgetExceeded, ConflictError
from ratchet.models import Purpose, Tier
from ratchet.store import Store

__all__ = [
    "CONCURRENCY",
    "DEFAULT_CONCURRENCY",
    "check_tier",
    "check_budget",
    "remaining_allowance",
    "split_allowance",
    "SidecarPolicy",
    "DAILY",
    "utc_day_start",
    "Limiter",
]

DAILY = "daily"  # allowance key for the all-purpose daily cap; other keys are purposes

# Per-purpose concurrency limits; purposes not listed share the default.
# Adjudicate is 4 rather than 2: Token Factory limits auto-scale and the live verification
# runs saw no 429s, while 4 contested FX pairs at 2-way made Ultra the slowest stage
# (docs/VERIFIED.md).
CONCURRENCY: dict[str, int] = {"agent": 8, "screen": 16, "check": 4, "adjudicate": 4, "bench": 8}
DEFAULT_CONCURRENCY = 8


def check_tier(purpose: Purpose, tier: Tier, meta: RunMeta | None) -> None:
    if purpose == "agent" and meta is not None and tier != meta.agent_tier:
        raise ConflictError(
            "model tier differs from the run's agent tier",
            code="tier_mismatch",
            context={"run_id": meta.run_id, "tier": tier, "agent_tier": meta.agent_tier},
        )


def utc_day_start(at: datetime | None = None) -> datetime:
    moment = at or clock.now()
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _refuse(scope: str, spent: float, cap: float, purpose: str) -> BudgetExceeded:
    return BudgetExceeded(
        "spending cap reached",
        context={"scope": scope, "purpose": purpose, "spent_usd": round(spent, 6), "cap": cap},
    )


@dataclass(frozen=True)
class SidecarPolicy:
    """How a proxy running as a replay-worker sidecar differs from the control-plane proxy.

    Every call is the agent's: purpose is forced to ``agent`` and the tier to the run's
    ``agent_tier`` whatever the (sandboxed) caller's headers say; spend is checked against
    ``allowance_usd`` (remaining at dispatch) minus what this batch already spent; admin
    routes need ``admin_token``, which only the worker holds.
    """

    agent_tier: Tier
    allowance_usd: Mapping[str, float] | None = None
    admin_token: str | None = None


def remaining_allowance(store: Store, settings: Settings) -> dict[str, float]:
    """USD left under each cap right now, from the control-plane ledger (sync).

    Keys: ``daily`` (all purposes, today), each purpose with a configured daily cap, and
    ``bench`` (all-time hard cap). Never negative.
    """
    since = utc_day_start()
    out = {DAILY: settings.ratchet_daily_usd_cap - store.spend(since=since).total_usd}
    for purpose, cap in settings.ratchet_purpose_usd_caps.items():
        out[purpose] = cap - store.spend(since=since, purpose=purpose).total_usd
    bench_left = settings.ratchet_bench_usd_cap - store.spend(purpose="bench").total_usd
    out["bench"] = min(out.get("bench", bench_left), bench_left)
    return {key: round(max(value, 0.0), 6) for key, value in out.items()}


def split_allowance(allowance: Mapping[str, float], n_batches: int) -> dict[str, float]:
    """Batches of one dispatch run concurrently, so each gets an equal share."""
    n = max(n_batches, 1)
    return {key: round(value / n, 6) for key, value in allowance.items()}


def _check_allowance(store: Store, purpose: Purpose, allowance: Mapping[str, float]) -> None:
    """``store`` is the worker's scratch store: everything in it is this batch's spend."""
    if DAILY in allowance:
        spent = store.spend().total_usd
        if spent >= allowance[DAILY]:
            raise _refuse("daily", spent, allowance[DAILY], purpose)
    if purpose in allowance:
        spent = store.spend(purpose=purpose).total_usd
        if spent >= allowance[purpose]:
            raise _refuse("purpose", spent, allowance[purpose], purpose)


def check_budget(
    store: Store,
    settings: Settings,
    purpose: Purpose,
    *,
    allowance: Mapping[str, float] | None = None,
) -> None:
    """Refuse when spend so far has reached a cap (sync; call via ``asyncio.to_thread``).

    The bench cap is a hard, all-time cap; the overall and
    per-purpose caps reset at 00:00 UTC. With ``allowance`` (a
    sidecar), the remaining-at-dispatch amounts are enforced against ``store``'s spend.
    """
    if allowance is not None:
        _check_allowance(store, purpose, allowance)
        return
    since = utc_day_start()
    daily = store.spend(since=since).total_usd
    if daily >= settings.ratchet_daily_usd_cap:
        raise _refuse("daily", daily, settings.ratchet_daily_usd_cap, purpose)
    purpose_cap = settings.ratchet_purpose_usd_caps.get(purpose)
    if purpose_cap is not None:
        spent = store.spend(since=since, purpose=purpose).total_usd
        if spent >= purpose_cap:
            raise _refuse("purpose", spent, purpose_cap, purpose)
    if purpose == "bench":
        spent = store.spend(purpose="bench").total_usd
        if spent >= settings.ratchet_bench_usd_cap:
            raise _refuse("bench", spent, settings.ratchet_bench_usd_cap, purpose)


class Limiter:
    """One semaphore per purpose, created on first use inside the running loop."""

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        self._limits = dict(CONCURRENCY if limits is None else limits)
        self._sems: dict[str, asyncio.Semaphore] = {}

    def limit(self, purpose: str) -> int:
        return self._limits.get(purpose, DEFAULT_CONCURRENCY)

    def semaphore(self, purpose: str) -> asyncio.Semaphore:
        sem = self._sems.get(purpose)
        if sem is None:
            sem = asyncio.Semaphore(self.limit(purpose))
            self._sems[purpose] = sem
        return sem
