"""``GET /costs`` and ``GET /ticker`` (CONTRACTS §7).

Spend tiles are host-wide totals from the proxy's ``costs`` rows (``Store.spend``): today
(UTC), the last 7 days, by purpose and by day. ``last_edit`` and ``counterfactual`` come from
``judge.costs.verdict_costs`` for the newest verdict the caller can list. ``counterfactual``
(the headline savings line) covers the same population as the tiles: the judge spend of every
verdict created in the window (last 7 days, or ``from``/``to``), host-wide like the spend
tiles, vs those judge tokens priced on Ultra — so a fresh public session sees the host's
savings, not $0.00 (``last_edit`` no longer drives it). Embedding rows (pseudo-tier
``embed``) are not shown per tier. Each tile also carries the prompt-cache share: prompt
tokens by tier, the prompt-cache hits among them, and ``cache_share`` (hits / prompt).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from ratchet import clock
from ratchet.config.pricing import counterfactual_ultra_usd
from ratchet.dashboard_api.deps import ServicesDep, SessionDep
from ratchet.dashboard_api.errors import BadRequest
from ratchet.dashboard_api.queries import TERMINAL_STATES, visible_clause, visible_verdict_ids
from ratchet.dashboard_api.routes.meta import bench_report_url
from ratchet.dashboard_api.views import (
    CostsView,
    CostTile,
    CostWindow,
    Counterfactual,
    DayCost,
    LastEditCost,
    PurposeCost,
    TickerView,
)
from ratchet.judge.costs import JUDGE_PURPOSES, verdict_costs
from ratchet.models import TIERS, Tier
from ratchet.store import Store, TierTotals

__all__ = ["router"]

router = APIRouter(tags=["costs"])


def _tier(value: str) -> Tier | None:
    return next((t for t in TIERS if t == value), None)


def _tile(totals: TierTotals) -> CostTile:
    tokens = {t: totals.prompt_tokens[t] + totals.completion_tokens[t] for t in TIERS}
    return CostTile(
        usd_by_tier=dict(totals.usd),
        tokens_by_tier=tokens,
        prompt_tokens_by_tier=dict(totals.prompt_tokens),
        cached_prompt_tokens_by_tier=dict(totals.cached_prompt_tokens),
        cache_share=totals.cache_share,
    )


def _by_purpose(store: Store, since: str, until: str | None = None) -> list[PurposeCost]:
    rows = store.query(
        """SELECT purpose, tier, COUNT(*) AS calls, SUM(usd) AS usd,
                  SUM(prompt_tokens + completion_tokens) AS tokens
           FROM costs WHERE ts >= ? AND (? IS NULL OR ts < ?)
           GROUP BY purpose, tier ORDER BY purpose, tier""",
        (since, until, until),
    )
    out: list[PurposeCost] = []
    for row in rows:
        tier = _tier(str(row["tier"]))
        if tier is not None:
            out.append(
                PurposeCost(
                    purpose=str(row["purpose"]),
                    tier=tier,
                    calls=int(row["calls"]),
                    usd=float(row["usd"] or 0.0),
                    tokens=int(row["tokens"] or 0),
                )
            )
    return out


def _window_days(store: Store, since: str, until: str) -> list[tuple[str, dict[str, float]]]:
    rows = store.query(
        """SELECT substr(ts, 1, 10) AS day, tier, SUM(usd) AS usd FROM costs
           WHERE ts >= ? AND ts < ? GROUP BY day, tier ORDER BY day""",
        (since, until),
    )
    days: dict[str, dict[str, float]] = {}
    for r in rows:
        days.setdefault(str(r["day"]), {})[str(r["tier"])] = float(r["usd"] or 0.0)
    return sorted(days.items())


def _by_day(store: Store, window: tuple[datetime, datetime] | None = None) -> list[DayCost]:
    days: list[DayCost] = []
    raw = (
        store.spend_by_day(days=14)
        if window is None
        else _window_days(store, clock.iso(window[0]), clock.iso(window[1]))
    )
    for day, usd in raw:
        by_tier: dict[Tier, float] = {}
        for name, value in usd.items():
            tier = _tier(name)
            if tier is not None:
                by_tier[tier] = value
        days.append(DayCost(day=day, usd_by_tier=by_tier))
    return days


def _last_edit(store: Store, session_hash: str | None) -> LastEditCost | None:
    ids = visible_verdict_ids(store, session_hash, cap=1)
    if not ids:
        return None
    row = store.query_one("SELECT edit_id FROM verdicts WHERE verdict_id = ?", (ids[0],))
    if row is None:  # pragma: no cover - the id came from the same table
        return None
    edit_id = str(row["edit_id"])
    summary = verdict_costs(store, edit_id)
    return LastEditCost(
        usd_by_tier=summary.usd_by_tier,
        tokens_by_tier=summary.tokens_by_tier,
        edit_id=edit_id,
        verdict_id=ids[0],
        counterfactual_usd=summary.counterfactual_ultra_usd,
    )


def _counterfactual(store: Store, start: datetime, end: datetime | None) -> Counterfactual:
    """Σ ``verdict_costs`` over the verdicts created in ``[start, end)``, in one query: judge
    usd by tier and the all-Ultra price of the same judge tokens (pricing is linear)."""
    purposes = ",".join("?" for _ in JUDGE_PURPOSES)
    bounds = (clock.iso(start), None if end is None else clock.iso(end))
    window = "created_at >= ? AND (? IS NULL OR created_at < ?)"
    rows = store.query(
        f"""SELECT tier, SUM(usd) AS usd, SUM(prompt_tokens) AS pt,
                   SUM(completion_tokens) AS ct
            FROM costs WHERE purpose IN ({purposes}) AND edit_id IN
                (SELECT edit_id FROM verdicts WHERE {window})
            GROUP BY tier""",
        (*JUDGE_PURPOSES, bounds[0], bounds[1], bounds[1]),
    )
    count = store.query_one(
        f"SELECT COUNT(*) AS n FROM verdicts WHERE {window}", (bounds[0], bounds[1], bounds[1])
    )
    by_tier: dict[Tier, float] = {}
    prompt = completion = 0
    for row in rows:
        tier = _tier(str(row["tier"]))
        if tier is None:
            continue
        by_tier[tier] = float(row["usd"] or 0.0)
        prompt += int(row["pt"] or 0)
        completion += int(row["ct"] or 0)
    actual = sum(by_tier.values())
    ultra = counterfactual_ultra_usd(prompt, completion)
    saved = 1.0 - actual / ultra if ultra > 0 else 0.0
    return Counterfactual(
        actual_usd=round(actual, 8),
        all_ultra_usd=round(ultra, 8),
        saved_ratio=max(0.0, min(1.0, saved)),
        actual_usd_by_tier=by_tier,
        n_verdicts=int(count["n"]) if count is not None else 0,
    )


def _start_of_day() -> datetime:
    return clock.now().replace(hour=0, minute=0, second=0, microsecond=0)


def _costs(
    store: Store,
    session_hash: str | None,
    report_url: str | None,
    window: tuple[datetime, datetime] | None = None,
) -> CostsView:
    week_start = clock.now() - timedelta(days=7)
    last_edit = _last_edit(store, session_hash)
    span = window or (week_start, None)
    counterfactual = _counterfactual(store, span[0], span[1])
    window_view: CostWindow | None = None
    if window is None:
        by_purpose = _by_purpose(store, clock.iso(week_start))
    else:
        totals = _tile(store.spend(since=window[0], until=window[1]))
        window_view = CostWindow(
            usd_by_tier=totals.usd_by_tier,
            tokens_by_tier=totals.tokens_by_tier,
            prompt_tokens_by_tier=totals.prompt_tokens_by_tier,
            cached_prompt_tokens_by_tier=totals.cached_prompt_tokens_by_tier,
            cache_share=totals.cache_share,
            from_=window[0],
            to=window[1],
        )
        by_purpose = _by_purpose(store, clock.iso(window[0]), clock.iso(window[1]))
    return CostsView(
        today=_tile(store.spend(since=_start_of_day())),
        week=_tile(store.spend(since=week_start)),
        by_purpose=by_purpose,
        by_day=_by_day(store, window),
        last_edit=last_edit,
        counterfactual=counterfactual,
        bench_report_url=report_url,
        window=window_view,
    )


def _window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime] | None:
    """``[from, to)``; a bound left out defaults to 7 days before ``to`` / now. Naive
    timestamps are read as UTC; at most 366 days."""
    if start is None and end is None:
        return None
    end = _utc(end) if end is not None else clock.now()
    start = _utc(start) if start is not None else end - timedelta(days=7)
    if start >= end or end - start > timedelta(days=366):
        raise BadRequest("The from/to range is not valid (from < to, at most 366 days).")
    return start, end


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.get("/costs", response_model=CostsView)
async def get_costs(
    services: ServicesDep,
    session: SessionDep,
    start: Annotated[datetime | None, Query(alias="from")] = None,
    end: Annotated[datetime | None, Query(alias="to")] = None,
) -> CostsView:
    """Spend tiles; with ``from``/``to`` (ISO 8601) also the totals, purposes and days of
    that window (``window``, ``by_purpose``, ``by_day``)."""
    report = bench_report_url(services.settings)
    window = _window(start, end)
    return await asyncio.to_thread(_costs, services.store, session.hash, report, window)


def _ticker(store: Store, session_hash: str | None) -> TickerView:
    totals = store.spend(since=_start_of_day())
    clause, params = visible_clause(session_hash)
    marks = ",".join("?" for _ in TERMINAL_STATES)
    running = store.query_one(
        f"SELECT 1 FROM edits WHERE state NOT IN ({marks}) AND {clause} LIMIT 1",
        (*sorted(TERMINAL_STATES), *params),
    )
    return TickerView(
        usd_by_tier=dict(totals.usd), total_usd=totals.total_usd, live=running is not None
    )


@router.get("/ticker", response_model=TickerView)
async def get_ticker(services: ServicesDep, session: SessionDep) -> TickerView:
    """Today's spend by tier; ``live`` is true while an edit this caller can see is running."""
    return await asyncio.to_thread(_ticker, services.store, session.hash)
