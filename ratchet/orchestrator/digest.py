"""The weekly digest: one Ultra call over the week's verdicts.

``run_digest`` writes ``data/digests/<digest_id>.json`` (the ``Digest`` model), a
``digests`` row and a ``digest.written`` event. ``write_digest`` falls back to a
deterministic text when the model fails, so a digest is always produced. ``metrics`` is the
product metrics table for the period (the rows ``ratchet metrics`` prints); digests written
before it existed load with an empty table.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.judge.narrative import DigestInput, write_digest
from ratchet.llm.client import ModelClient
from ratchet.models import Verdict
from ratchet.orchestrator.context import InProcessProxy
from ratchet.orchestrator.metrics import product_metrics
from ratchet.store import Store
from ratchet.store.base import write_json_atomic
from ratchet.util import new_id

__all__ = ["Digest", "MetricRow", "latest", "run_digest", "load_digest"]


class MetricRow(BaseModel):
    metric: str
    value: str


class Digest(BaseModel):
    digest_id: str
    period_start: datetime
    period_end: datetime
    narrative: str
    verdict_ids: list[str]
    merged: list[str]
    blocked: list[str]
    usd: float
    simulated_models: bool
    metrics: list[MetricRow] = Field(default_factory=list)  # + product metrics table


def load_digest(store: Store, digest_id: str) -> Digest:
    path = store.data_dir / "digests" / f"{digest_id}.json"
    return Digest.model_validate_json(path.read_text(encoding="utf-8"))


def latest(store: Store) -> Digest | None:
    row = store.query_one("SELECT digest_id FROM digests ORDER BY created_at DESC LIMIT 1")
    if row is None:
        return None
    digest_id = str(row["digest_id"])
    if not (store.data_dir / "digests" / f"{digest_id}.json").is_file():
        return None
    return load_digest(store, digest_id)


def _verdicts(store: Store, start: datetime) -> list[Verdict]:
    rows = store.list_verdicts(limit=10_000)
    return [store.load_verdict(r.verdict_id) for r in rows if r.created_at >= start]


def _save(store: Store, digest: Digest) -> None:
    write_json_atomic(store.data_dir / "digests" / f"{digest.digest_id}.json", digest)
    with store.tx() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO digests (digest_id, period_start, period_end, created_at) "
            "VALUES (?,?,?,?)",
            (
                digest.digest_id,
                clock.iso(digest.period_start),
                clock.iso(digest.period_end),
                clock.iso(clock.now()),
            ),
        )


async def run_digest(store: Store, settings: Settings, *, days: int = 7) -> Digest:
    end = clock.now()
    start = end - timedelta(days=days)
    verdicts = await asyncio.to_thread(_verdicts, store, start)
    spend = await asyncio.to_thread(store.spend, since=start, until=end)
    table = await asyncio.to_thread(product_metrics, store, start)
    digest_id = new_id("digest")
    proxy = InProcessProxy(settings, store)
    client = ModelClient(run_id=f"digest_{digest_id[7:]}", settings=settings, http=proxy.client)
    try:
        inp = DigestInput(
            period_start=start.date().isoformat(),
            period_end=end.date().isoformat(),
            verdicts=sorted(verdicts, key=lambda v: v.created_at),
            cost_by_tier=dict(spend.usd),
        )
        result = await write_digest(inp, client)
    finally:
        await proxy.aclose()
    digest = Digest(
        digest_id=digest_id,
        period_start=start,
        period_end=end,
        narrative=result.text,
        verdict_ids=[v.verdict_id for v in verdicts],
        merged=[v.verdict_id for v in verdicts if v.action == "merged"],
        blocked=[v.verdict_id for v in verdicts if v.decision == "BLOCK"],
        usd=round(spend.total_usd, 6),
        simulated_models=settings.simulated,
        metrics=[MetricRow(metric=m, value=v) for m, v in table],
    )
    await asyncio.to_thread(_save, store, digest)
    payload: dict[str, object] = {
        "digest_id": digest_id,
        "period": {"start": clock.iso(start), "end": clock.iso(end)},
        "verdict_ids": digest.verdict_ids,
    }
    await asyncio.to_thread(store.emit, "digest.written", digest_id, payload)
    return digest
