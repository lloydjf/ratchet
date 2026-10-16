"""Text reports for ``ratchet status``, ``status --timing``, ``logs --edit`` and ``metrics``.

``status`` ends with today's spend and prompt-cache share (hits / prompt tokens, per tier)."""

from __future__ import annotations

import json
from datetime import datetime

from ratchet import clock
from ratchet.cli.common import short, table
from ratchet.models import TIERS
from ratchet.orchestrator.journal import STATES, TERMINAL
from ratchet.orchestrator.metrics import demo_funnel, product_metrics
from ratchet.store import Store, TierTotals

__all__ = ["status_text", "timing_text", "logs_text", "metrics_text", "cache_share_text"]


def status_text(store: Store) -> str:
    in_flight = store.edits_in_state(tuple(s for s in STATES if s not in TERMINAL))
    lines = ["Edits in flight:"]
    if in_flight:
        rows = [(e.edit_id, e.state, clock.iso(e.state_updated_at)) for e in in_flight]
        lines.append(table(["edit", "state", "since"], rows))
    else:
        lines.append("  none")
    verdicts = store.list_verdicts(limit=5)
    lines.append("\nLast verdicts:")
    if verdicts:
        rows2 = []
        for row in verdicts:
            v = store.load_verdict(row.verdict_id)
            rows2.append((v.verdict_id, v.decision, v.action, short(v.headline, 70)))
        lines.append(table(["verdict", "decision", "action", "headline"], rows2))
    else:
        lines.append("  none")
    today = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
    spend = store.spend(since=today)
    by_tier = ", ".join(f"${spend.usd[t]:.4f} ({t})" for t in TIERS)
    lines.append(f"\nSpend today: ${spend.total_usd:.4f} — {by_tier}")
    lines.append(f"Prompt-cache share today: {cache_share_text(spend)}")
    return "\n".join(lines)


def _share(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def cache_share_text(totals: TierTotals) -> str:
    """``0.42 of prompt tokens were cache hits (nano n/a, super 0.51, ultra 0.00)``."""
    by_tier = ", ".join(f"{t} {_share(totals.tier_cache_share(t))}" for t in TIERS)
    return f"{_share(totals.cache_share)} of prompt tokens were cache hits ({by_tier})"


def timing_text(store: Store, verdict_id: str) -> str:
    verdict = store.load_verdict(verdict_id)
    rows = [(s.stage, f"{s.elapsed_ms} ms") for s in verdict.stages]
    total = sum(s.elapsed_ms for s in verdict.stages)
    return "\n".join(
        [
            f"{verdict.verdict_id}: {verdict.decision} in {verdict.duration_s} s",
            table(["stage", "elapsed"], rows),
            f"total {total} ms",
        ]
    )


def logs_text(store: Store, edit_id: str) -> str:
    edit = store.load_edit(edit_id)
    events = [
        e
        for e in store.events(session_hash=edit.session_hash, limit=1000)
        if e.ref == edit_id or e.payload.get("edit_id") == edit_id
    ]
    lines = [f"{clock.iso(e.ts)} {e.kind} {_fields(e.payload)}" for e in events]
    for crash in sorted(store.edit_dir(edit_id).glob("attempt_*/crash.json")):
        info = json.loads(crash.read_text(encoding="utf-8"))
        lines.append(
            f"--- crash in {info.get('state')}: {info.get('error_type')}: {info.get('message')}"
        )
        lines.extend(f"    {frame}" for frame in info.get("frames", []))
    for batch in store.batches_for_edit(edit_id):
        log = store.batch_dir(batch.batch_id) / "worker.log"
        if log.is_file():
            lines.append(f"--- worker log {batch.batch_id}")
            lines.extend(log.read_text(encoding="utf-8").splitlines())
    return "\n".join(lines) or "no events for this edit"


def _fields(payload: dict[str, object]) -> str:
    keep = {k: v for k, v in payload.items() if k != "edit_id" and not isinstance(v, dict | list)}
    return " ".join(f"{k}={v}" for k, v in sorted(keep.items()))


def metrics_text(store: Store, since: datetime, *, demo: bool) -> str:
    rows = demo_funnel(store, since) if demo else product_metrics(store, since)
    title = "Demo funnel" if demo else "Product metrics"
    return f"{title} since {since.date().isoformat()}\n" + table(["metric", "value"], rows)
