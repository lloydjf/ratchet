"""Product metrics and the anonymous demo funnel, computed from the store.

``product_metrics`` reads verdicts and events (``trace.blessed``); ``demo_funnel`` reads the
``demo_events`` table. Both return ``(name, value)`` rows for ``ratchet metrics``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from datetime import datetime

from ratchet.errors import NotFoundError
from ratchet.models import TIERS, Verdict
from ratchet.store import Store

__all__ = ["product_metrics", "demo_funnel", "FUNNEL_STEPS"]

FUNNEL_STEPS: tuple[str, ...] = (
    "demo.landing_view",
    "demo.simulate_click",
    "demo.verdict_view",
    "demo.evidence_open",
    "demo.action_click",
)


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.0f}% ({part}/{whole})" if whole else "n/a"


def _verdicts(store: Store, since: datetime) -> list[Verdict]:
    rows = store.list_verdicts(limit=10_000)
    return [store.load_verdict(r.verdict_id) for r in rows if r.created_at >= since]


def _world_dependent(store: Store, run_id: str, cache: dict[str, bool]) -> bool:
    if run_id not in cache:
        try:
            cache[run_id] = store.load_trace(run_id).world_dependent
        except NotFoundError:
            cache[run_id] = False
    return cache[run_id]


def _percentile(values: list[int], q: float) -> str:
    if not values:
        return "n/a"
    ordered = sorted(values)
    index = min(int(round(q * (len(ordered) - 1))), len(ordered) - 1)
    return f"{ordered[index]} s"


def _pairs_rows(store: Store, verdicts: list[Verdict]) -> list[tuple[str, str]]:
    pairs = [p for v in verdicts for p in v.pairs]
    cache: dict[str, bool] = {}
    world = [p for p in pairs if _world_dependent(store, p.golden_run_id, cache)]
    opinions = [o for p in pairs for o in p.opinions]
    return [
        ("Drift share", _pct(sum(p.outcome == "drift" for p in world), len(world))),
        ("Uncertain rate", _pct(sum(p.outcome == "uncertain" for p in pairs), len(pairs))),
        ("Evidence quality (truncated)", _pct(sum(o.truncated for o in opinions), len(opinions))),
    ]


def _cost_rows(verdicts: list[Verdict]) -> list[tuple[str, str]]:
    if not verdicts:
        return [("Cost per verdict", "n/a")]
    by_tier = {t: statistics.fmean(v.cost.get(t, 0.0) for v in verdicts) for t in TIERS}
    counterfactual = statistics.fmean(v.counterfactual_usd for v in verdicts)
    text = ", ".join(f"${usd:.4f} ({tier})" for tier, usd in by_tier.items())
    replay = statistics.fmean(sum(v.replay_cost.values()) for v in verdicts)
    return [
        ("Judging cost per verdict", text),
        ("All-Ultra counterfactual per verdict", f"${counterfactual:.4f}"),
        ("Replay cost per verdict (agent)", f"${replay:.4f}"),
    ]


def product_metrics(store: Store, since: datetime) -> list[tuple[str, str]]:
    verdicts = _verdicts(store, since)
    blessed = [e for e in store.events(kinds=["trace.blessed"], limit=1000) if e.ts >= since]
    decisions = Counter(v.decision for v in verdicts)
    blocks = [v for v in verdicts if v.decision == "BLOCK"]
    flagged = [v for v in verdicts if v.decision in ("WARN", "BLOCK")]
    mix = ", ".join(f"{d} {_pct(decisions[d], len(verdicts))}" for d in ("PASS", "WARN", "BLOCK"))
    durations = [v.duration_s for v in verdicts]
    rows = [
        ("Blessed traces", str(len(blessed))),
        ("Protected edits", str(sum(bool(v.pairs) for v in verdicts))),
        ("Decision mix", mix if verdicts else "n/a"),
        ("Block precision proxy", _pct(sum(v.approved_at is None for v in blocks), len(blocks))),
        ("False-alarm proxy", _pct(sum(v.approved_at is not None for v in flagged), len(flagged))),
        ("Edit to verdict p50", _percentile(durations, 0.5)),
        ("Edit to verdict p95", _percentile(durations, 0.95)),
    ]
    return rows + _pairs_rows(store, verdicts) + _cost_rows(verdicts)


def demo_funnel(store: Store, since: datetime) -> list[tuple[str, str]]:
    events = [e for e in store.events(table="demo_events", limit=1000) if e.ts >= since]
    sessions: dict[str, set[str | None]] = {k: set() for k in FUNNEL_STEPS}
    for event in events:
        if event.kind in sessions:
            sessions[event.kind].add(event.session_hash)
    rows: list[tuple[str, str]] = []
    previous: int | None = None
    for step in FUNNEL_STEPS:
        n = len(sessions[step])
        conversion = f" ({_pct(n, previous)} of the previous step)" if previous else ""
        rows.append((step, f"{n}{conversion}"))
        previous = n
    return rows
