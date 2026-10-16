"""Events (the timeline spine), costs and caches."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from ratchet import clock
from ratchet.models import TIERS, Event, Tier
from ratchet.store.base import StoreBase

__all__ = ["LedgerStore", "CostRow", "TierTotals"]


def _as_tier(value: str) -> Tier | None:
    for tier in TIERS:
        if tier == value:
            return tier
    return None


@dataclass(frozen=True)
class CostRow:
    call_id: str
    run_id: str
    edit_id: str | None
    tier: Tier | Literal["embed"]  # embed is a pseudo-tier (ratchet/proxy/embeddings.py)
    model: str
    prompt_tokens: int
    completion_tokens: int
    usd: float
    purpose: str
    cached: bool = False  # served from Ratchet's response cache (nothing spent)
    session_hash: str | None = None
    cached_prompt_tokens: int = 0  # prompt tokens the upstream's prompt cache served


def _share(cached: int, prompt: int) -> float | None:
    return cached / prompt if prompt > 0 else None


@dataclass(frozen=True)
class TierTotals:
    usd: dict[Tier, float]
    prompt_tokens: dict[Tier, int]
    completion_tokens: dict[Tier, int]
    cached_prompt_tokens: dict[Tier, int] = field(default_factory=lambda: dict.fromkeys(TIERS, 0))

    @property
    def total_usd(self) -> float:
        return sum(self.usd.values())

    @property
    def cache_share(self) -> float | None:
        """Prompt-cache hits over prompt tokens, all tiers; ``None`` when no prompt tokens."""
        return _share(sum(self.cached_prompt_tokens.values()), sum(self.prompt_tokens.values()))

    def tier_cache_share(self, tier: Tier) -> float | None:
        return _share(self.cached_prompt_tokens.get(tier, 0), self.prompt_tokens.get(tier, 0))


class LedgerStore(StoreBase):
    # -- events ---------------------------------------------------------------------------
    def emit(
        self,
        kind: str,
        ref: str,
        payload: dict[str, object] | None = None,
        *,
        session_hash: str | None = None,
        table: str = "events",
    ) -> int:
        if table not in {"events", "demo_events"}:
            raise ValueError("unknown events table")
        with self.tx() as conn:
            cur = conn.execute(
                f"INSERT INTO {table} (ts, kind, ref, payload_json, session_hash) "
                "VALUES (?,?,?,?,?)",
                (
                    clock.iso(clock.now()),
                    kind,
                    ref,
                    json.dumps(payload or {}, default=str, sort_keys=True),
                    session_hash,
                ),
            )
            return int(cur.lastrowid or 0)

    def events(
        self,
        *,
        since: int = 0,
        kinds: Iterable[str] | None = None,
        session_hash: str | None = None,
        include_global: bool = True,
        limit: int = 200,
        newest_first: bool = False,
        before: int | None = None,
        table: str = "events",
        ref: str | None = None,
    ) -> list[Event]:
        if table not in {"events", "demo_events"}:
            raise ValueError("unknown events table")
        clauses = ["id > ?"]
        params: list[object] = [since]
        if ref is not None:
            clauses.append("ref = ?")
            params.append(ref)
        if before is not None:
            clauses.append("id < ?")
            params.append(before)
        kind_list = [k for k in (kinds or []) if k]
        if kind_list:
            clauses.append(f"kind IN ({','.join('?' for _ in kind_list)})")
            params.extend(kind_list)
        if session_hash is not None:
            clauses.append(
                "(session_hash = ?" + (" OR session_hash IS NULL)" if include_global else ")")
            )
            params.append(session_hash)
        elif not include_global:
            clauses.append("session_hash IS NULL")
        order = "DESC" if newest_first else "ASC"
        rows = self.query(
            f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY id {order} LIMIT ?",
            (*params, min(max(limit, 1), 1000)),
        )
        return [
            Event(
                id=int(r["id"]),
                ts=clock.parse(str(r["ts"])),
                kind=str(r["kind"]),
                ref=str(r["ref"]),
                payload=json.loads(str(r["payload_json"])),
                session_hash=r["session_hash"],
            )
            for r in rows
        ]

    def prune_demo_events(self, *, older_than_days: int = 30) -> int:
        cutoff = clock.iso(clock.now() - timedelta(days=older_than_days))
        with self.tx() as conn:
            return int(conn.execute("DELETE FROM demo_events WHERE ts < ?", (cutoff,)).rowcount)

    # -- costs ----------------------------------------------------------------------------
    def record_cost(self, row: CostRow) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO costs (call_id, run_id, edit_id, tier, model,
                       prompt_tokens, completion_tokens, usd, purpose, ts, cached, session_hash,
                       cached_prompt_tokens)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    row.call_id,
                    row.run_id,
                    row.edit_id,
                    row.tier,
                    row.model,
                    row.prompt_tokens,
                    row.completion_tokens,
                    row.usd,
                    row.purpose,
                    clock.iso(clock.now()),
                    int(row.cached),
                    row.session_hash,
                    max(0, min(row.cached_prompt_tokens, row.prompt_tokens)),
                ),
            )

    def spend(
        self,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        purpose: str | None = None,
        edit_id: str | None = None,
        purposes: Iterable[str] | None = None,
    ) -> TierTotals:
        clauses = ["1=1"]
        params: list[object] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(clock.iso(since))
        if until is not None:
            clauses.append("ts < ?")
            params.append(clock.iso(until))
        if purpose is not None:
            clauses.append("purpose = ?")
            params.append(purpose)
        purpose_list = list(purposes or [])
        if purpose_list:
            clauses.append(f"purpose IN ({','.join('?' for _ in purpose_list)})")
            params.extend(purpose_list)
        if edit_id is not None:
            clauses.append("edit_id = ?")
            params.append(edit_id)
        rows = self.query(
            f"""SELECT tier, SUM(usd) AS usd, SUM(prompt_tokens) AS pt,
                       SUM(completion_tokens) AS ct, SUM(cached_prompt_tokens) AS cpt
                FROM costs WHERE {" AND ".join(clauses)} GROUP BY tier""",
            tuple(params),
        )
        usd: dict[Tier, float] = dict.fromkeys(TIERS, 0.0)
        pt: dict[Tier, int] = dict.fromkeys(TIERS, 0)
        ct: dict[Tier, int] = dict.fromkeys(TIERS, 0)
        cpt: dict[Tier, int] = dict.fromkeys(TIERS, 0)
        for r in rows:
            tier = _as_tier(str(r["tier"]))
            if tier is not None:
                usd[tier] = float(r["usd"] or 0.0)
                pt[tier] = int(r["pt"] or 0)
                ct[tier] = int(r["ct"] or 0)
                cpt[tier] = int(r["cpt"] or 0)
        return TierTotals(usd=usd, prompt_tokens=pt, completion_tokens=ct, cached_prompt_tokens=cpt)

    def spend_by_purpose(self, *, since: datetime | None = None) -> dict[str, dict[str, float]]:
        rows = self.query(
            """SELECT purpose, tier, SUM(usd) AS usd FROM costs WHERE (? IS NULL OR ts >= ?)
               GROUP BY purpose, tier""",
            (clock.iso(since) if since else None, clock.iso(since) if since else None),
        )
        out: dict[str, dict[str, float]] = {}
        for r in rows:
            out.setdefault(str(r["purpose"]), {})[str(r["tier"])] = float(r["usd"] or 0.0)
        return out

    def spend_by_day(self, *, days: int = 14) -> list[tuple[str, dict[str, float]]]:
        since = clock.now() - timedelta(days=days)
        rows = self.query(
            """SELECT substr(ts, 1, 10) AS day, tier, SUM(usd) AS usd FROM costs WHERE ts >= ?
               GROUP BY day, tier ORDER BY day""",
            (clock.iso(since),),
        )
        days_map: dict[str, dict[str, float]] = {}
        for r in rows:
            days_map.setdefault(str(r["day"]), {})[str(r["tier"])] = float(r["usd"] or 0.0)
        return sorted(days_map.items())

    # -- caches -----------------------------------------------------------------------------
    def cache_get(self, table: str, key: str, *, ttl: timedelta | None = None) -> object | None:
        if table not in {"response_cache", "tavily_cache"}:
            raise ValueError("unknown cache table")
        row = self.query_one(f"SELECT * FROM {table} WHERE cache_key = ?", (key,))
        if row is None:
            return None
        if ttl is not None and clock.parse(str(row["created_at"])) < clock.now() - ttl:
            return None
        value: object = json.loads(str(row["response_json"]))
        return value

    def cache_count_since(self, table: str, since: datetime) -> int:
        """Entries written at or after ``since``: live calls made (hits never write)."""
        if table not in {"response_cache", "tavily_cache"}:
            raise ValueError("unknown cache table")
        row = self.query_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE created_at >= ?", (clock.iso(since),)
        )
        return int(row["n"]) if row is not None else 0

    def cache_put(self, table: str, key: str, value: object, *, model: str = "") -> None:
        if table not in {"response_cache", "tavily_cache"}:
            raise ValueError("unknown cache table")
        columns = (
            "cache_key, model, response_json, created_at"
            if table == "response_cache"
            else ("cache_key, response_json, created_at")
        )
        values: tuple[object, ...] = (
            (key, model, json.dumps(value, default=str), clock.iso(clock.now()))
            if table == "response_cache"
            else (key, json.dumps(value, default=str), clock.iso(clock.now()))
        )
        marks = ",".join("?" for _ in values)
        with self.tx() as conn:
            conn.execute(f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({marks})", values)

    def side_effect_cached(self, tool: str) -> tuple[str, str] | None:
        row = self.query_one(
            "SELECT side_effect, reason FROM side_effect_cache WHERE tool = ?", (tool,)
        )
        return (str(row["side_effect"]), str(row["reason"])) if row else None

    def cache_side_effect(self, tool: str, side_effect: str, reason: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO side_effect_cache VALUES (?,?,?,?)",
                (tool, side_effect, reason, clock.iso(clock.now())),
            )
