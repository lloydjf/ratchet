"""SQLite index, WAL mode, forward-only idempotent migrations.

Bodies (traces, verdicts, cassettes) live on disk; SQLite holds indexes, the events
spine, costs, caches and orchestrator state. A connection is opened per unit of work so
calls are safe from any thread (``asyncio.to_thread``) and any process.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

__all__ = ["connect", "transaction", "migrate", "SCHEMA_VERSION"]

_MIGRATIONS: list[str] = [
    # 1 — the minimum tables plus orchestrator state and caches.
    """
    CREATE TABLE IF NOT EXISTS traces (
        run_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK (kind IN ('live', 'golden', 'replay')),
        golden_of TEXT,
        candidate_commit TEXT,
        world_dependent INTEGER NOT NULL DEFAULT 0,
        agent_tier TEXT NOT NULL,
        created_at TEXT NOT NULL,
        blessed_at TEXT,
        archived_reason TEXT,
        task_label TEXT NOT NULL DEFAULT '',
        n_facts INTEGER NOT NULL DEFAULT 0,
        last_replayed_at TEXT
    );
    CREATE INDEX IF NOT EXISTS traces_kind ON traces(kind, archived_reason);
    CREATE INDEX IF NOT EXISTS traces_golden_of ON traces(golden_of);
    CREATE TABLE IF NOT EXISTS trace_skills (
        run_id TEXT NOT NULL,
        skill TEXT NOT NULL,
        PRIMARY KEY (run_id, skill)
    );
    CREATE INDEX IF NOT EXISTS trace_skills_skill ON trace_skills(skill);
    CREATE TABLE IF NOT EXISTS edits (
        edit_id TEXT PRIMARY KEY,
        "commit" TEXT NOT NULL,
        parent TEXT NOT NULL,
        detected_at TEXT NOT NULL,
        author TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'detected',
        state_updated_at TEXT NOT NULL,
        session_hash TEXT,
        affected_json TEXT NOT NULL DEFAULT '[]',
        verdict_id TEXT,
        error_code TEXT
    );
    CREATE INDEX IF NOT EXISTS edits_state ON edits(state);
    CREATE TABLE IF NOT EXISTS edit_traces (
        edit_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        reason TEXT NOT NULL CHECK (reason IN ('direct', 'neighborhood', 'canary')),
        similarity REAL,
        PRIMARY KEY (edit_id, run_id)
    );
    CREATE TABLE IF NOT EXISTS batches (
        batch_id TEXT PRIMARY KEY,
        edit_id TEXT NOT NULL,
        status TEXT NOT NULL,
        runner TEXT NOT NULL DEFAULT 'local',
        job_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        submitted_at TEXT,
        finished_at TEXT,
        last_heartbeat_at TEXT,
        progress_k INTEGER NOT NULL DEFAULT 0,
        progress_n INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS batches_edit ON batches(edit_id);
    CREATE TABLE IF NOT EXISTS verdicts (
        verdict_id TEXT PRIMARY KEY,
        edit_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        action TEXT NOT NULL,
        usd_total REAL NOT NULL,
        created_at TEXT NOT NULL,
        session_hash TEXT,
        acting INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS verdicts_created ON verdicts(created_at);
    CREATE TABLE IF NOT EXISTS costs (
        call_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        edit_id TEXT,
        tier TEXT NOT NULL,
        model TEXT NOT NULL DEFAULT '',
        prompt_tokens INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        usd REAL NOT NULL,
        purpose TEXT NOT NULL,
        ts TEXT NOT NULL,
        cached INTEGER NOT NULL DEFAULT 0,
        session_hash TEXT
    );
    CREATE INDEX IF NOT EXISTS costs_ts ON costs(ts);
    CREATE INDEX IF NOT EXISTS costs_edit ON costs(edit_id);
    CREATE INDEX IF NOT EXISTS costs_run ON costs(run_id);
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        session_hash TEXT
    );
    CREATE INDEX IF NOT EXISTS events_kind ON events(kind);
    CREATE INDEX IF NOT EXISTS events_session ON events(session_hash);
    CREATE TABLE IF NOT EXISTS demo_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        session_hash TEXT
    );
    CREATE TABLE IF NOT EXISTS response_cache (
        cache_key TEXT PRIMARY KEY,
        model TEXT NOT NULL,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS tavily_cache (
        cache_key TEXT PRIMARY KEY,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS side_effect_cache (
        tool TEXT PRIMARY KEY,
        side_effect TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS simulate_sessions (
        session_hash TEXT NOT NULL,
        scenario TEXT NOT NULL,
        edit_id TEXT,
        verdict_id TEXT,
        status TEXT NOT NULL,
        started_at TEXT NOT NULL,
        reset_at TEXT NOT NULL,
        PRIMARY KEY (session_hash, scenario)
    );
    CREATE TABLE IF NOT EXISTS digests (
        digest_id TEXT PRIMARY KEY,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """,
    # 2 — events by primary id (the orchestrator's progress view reads one edit's events).
    """
    CREATE INDEX IF NOT EXISTS events_ref ON events(ref);
    """,
    # 3 — when the per-verdict action lock was taken, so a crashed holder's lock expires.
    """
    ALTER TABLE verdicts ADD COLUMN acting_at TEXT;
    """,
    # 4 — prompt tokens the upstream served from its prompt (prefix) cache.
    """
    ALTER TABLE costs ADD COLUMN cached_prompt_tokens INTEGER NOT NULL DEFAULT 0;
    """,
]

SCHEMA_VERSION = len(_MIGRATIONS)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextlib.contextmanager
def transaction(db_path: Path, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """One connection, one transaction. ``immediate`` takes the write lock up front
    (action endpoints take a per-verdict lock with ``BEGIN IMMEDIATE``)."""
    conn = connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def migrate(db_path: Path) -> int:
    """Apply pending migrations; safe to run on every start. Returns the schema version."""
    conn = connect(db_path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        current = int(row["v"]) if row and row["v"] is not None else 0
        for index, script in enumerate(_MIGRATIONS[current:], start=current + 1):
            conn.executescript(
                "BEGIN;" + script + f"INSERT INTO schema_version VALUES ({index});COMMIT;"
            )
        return SCHEMA_VERSION
    finally:
        conn.close()
