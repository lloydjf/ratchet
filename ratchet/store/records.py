"""Edits, selections, batches, verdicts and Simulate sessions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

from ratchet import clock
from ratchet.errors import ConflictError, NotFoundError
from ratchet.models import ReplayBatch, ReplayResult, SkillEdit, Verdict
from ratchet.store.base import StoreBase, write_json_atomic

__all__ = [
    "RecordStore",
    "EditState",
    "SelectedTrace",
    "BatchRow",
    "VerdictRow",
    "SessionRow",
    "ACTION_LOCK_TTL_S",
]

ACTION_LOCK_TTL_S = 600.0  # an action (git revert/merge) never legitimately takes this long


def _lock_expired(acting_at: object, now: datetime) -> bool:
    if not acting_at:
        return True
    return (now - clock.parse(str(acting_at))).total_seconds() > ACTION_LOCK_TTL_S


@dataclass(frozen=True)
class EditState:
    edit_id: str
    state: str
    state_updated_at: datetime
    verdict_id: str | None
    error_code: str | None
    session_hash: str | None


@dataclass(frozen=True)
class SelectedTrace:
    run_id: str
    reason: str  # direct | neighborhood | canary
    similarity: float | None


@dataclass(frozen=True)
class BatchRow:
    batch_id: str
    edit_id: str
    status: str
    runner: str
    job_id: str | None
    attempts: int
    progress_k: int
    progress_n: int
    last_heartbeat_at: datetime | None


@dataclass(frozen=True)
class VerdictRow:
    verdict_id: str
    edit_id: str
    decision: str
    action: str
    usd_total: float
    created_at: datetime
    session_hash: str | None


@dataclass(frozen=True)
class SessionRow:
    session_hash: str
    scenario: str
    edit_id: str | None
    verdict_id: str | None
    status: str
    started_at: datetime
    reset_at: datetime


class RecordStore(StoreBase):
    # -- edits ----------------------------------------------------------------------------
    def save_edit(self, edit: SkillEdit, diff_text: str) -> SkillEdit:
        edit_dir = self.edit_dir(edit.edit_id)
        edit_dir.mkdir(parents=True, exist_ok=True)
        (edit_dir / "diff.patch").write_text(diff_text, encoding="utf-8")
        edit = edit.model_copy(update={"diff_ref": f"edits/{edit.edit_id}/diff.patch"})
        write_json_atomic(edit_dir / "edit.json", edit)
        now = clock.iso(clock.now())
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO edits (edit_id, "commit", parent, detected_at, author, state,
                       state_updated_at, session_hash, affected_json)
                   VALUES (?,?,?,?,?, 'detected', ?, ?, ?)
                   ON CONFLICT(edit_id) DO NOTHING""",
                (
                    edit.edit_id,
                    edit.commit,
                    edit.parent,
                    clock.iso(edit.detected_at),
                    edit.author,
                    now,
                    edit.session_hash,
                    json.dumps(edit.affected_skills),
                ),
            )
        return edit

    def load_edit(self, edit_id: str) -> SkillEdit:
        path = self.edit_dir(edit_id) / "edit.json"
        if not path.exists():
            raise NotFoundError(f"edit {edit_id} not found")
        return self.load_model(path, SkillEdit)

    def load_diff(self, edit_id: str) -> str:
        path = self.edit_dir(edit_id) / "diff.patch"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def list_edits(self, *, session_hash: str | None = None, limit: int = 100) -> list[SkillEdit]:
        rows = self.query(
            """SELECT edit_id FROM edits WHERE (? IS NULL OR session_hash IS NULL
                   OR session_hash = ?) ORDER BY detected_at DESC LIMIT ?""",
            (session_hash, session_hash, limit),
        )
        return [self.load_edit(str(row["edit_id"])) for row in rows]

    def set_edit_state(
        self,
        edit_id: str,
        state: str,
        *,
        verdict_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self.tx() as conn:
            cur = conn.execute(
                """UPDATE edits SET state = ?, state_updated_at = ?,
                       verdict_id = COALESCE(?, verdict_id), error_code = COALESCE(?, error_code)
                   WHERE edit_id = ?""",
                (state, clock.iso(clock.now()), verdict_id, error_code, edit_id),
            )
            if cur.rowcount == 0:
                raise NotFoundError(f"edit {edit_id} not found")

    def edit_state(self, edit_id: str) -> EditState:
        row = self.query_one("SELECT * FROM edits WHERE edit_id = ?", (edit_id,))
        if row is None:
            raise NotFoundError(f"edit {edit_id} not found")
        return EditState(
            edit_id=str(row["edit_id"]),
            state=str(row["state"]),
            state_updated_at=clock.parse(str(row["state_updated_at"])),
            verdict_id=row["verdict_id"],
            error_code=row["error_code"],
            session_hash=row["session_hash"],
        )

    def edits_in_state(self, states: tuple[str, ...]) -> list[EditState]:
        marks = ",".join("?" for _ in states)
        rows = self.query(
            f"SELECT edit_id FROM edits WHERE state IN ({marks}) ORDER BY detected_at", states
        )
        return [self.edit_state(str(row["edit_id"])) for row in rows]

    # -- selection ------------------------------------------------------------------------
    def save_selection(self, edit_id: str, selected: list[SelectedTrace]) -> None:
        with self.tx() as conn:
            conn.execute("DELETE FROM edit_traces WHERE edit_id = ?", (edit_id,))
            conn.executemany(
                "INSERT INTO edit_traces (edit_id, run_id, reason, similarity) VALUES (?,?,?,?)",
                [(edit_id, s.run_id, s.reason, s.similarity) for s in selected],
            )

    def load_selection(self, edit_id: str) -> list[SelectedTrace]:
        rows = self.query(
            "SELECT run_id, reason, similarity FROM edit_traces WHERE edit_id = ? ORDER BY rowid",
            (edit_id,),
        )
        return [SelectedTrace(str(r["run_id"]), str(r["reason"]), r["similarity"]) for r in rows]

    # -- batches --------------------------------------------------------------------------
    def save_batch_spec(self, spec: ReplayBatch, runner: str) -> None:
        write_json_atomic(self.batch_dir(spec.batch_id) / "spec.json", spec)
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO batches (batch_id, edit_id, status, runner, progress_n)
                   VALUES (?, ?, 'pending', ?, ?) ON CONFLICT(batch_id) DO NOTHING""",
                (spec.batch_id, spec.edit_id, runner, len(spec.golden_run_ids)),
            )

    def load_batch_spec(self, batch_id: str) -> ReplayBatch:
        return self.load_model(self.batch_dir(batch_id) / "spec.json", ReplayBatch)

    def update_batch(self, batch_id: str, **fields: object) -> None:
        allowed = {
            "status",
            "runner",
            "job_id",
            "attempts",
            "submitted_at",
            "finished_at",
            "last_heartbeat_at",
            "progress_k",
            "progress_n",
        }
        if not fields or set(fields) - allowed:
            raise ValueError(f"bad batch fields {sorted(fields)}")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        values = [clock.iso(v) if isinstance(v, datetime) else v for v in fields.values()]
        with self.tx() as conn:
            conn.execute(
                f"UPDATE batches SET {assignments} WHERE batch_id = ?", (*values, batch_id)
            )

    def batches_for_edit(self, edit_id: str) -> list[BatchRow]:
        rows = self.query("SELECT * FROM batches WHERE edit_id = ? ORDER BY batch_id", (edit_id,))
        return [
            BatchRow(
                batch_id=str(r["batch_id"]),
                edit_id=str(r["edit_id"]),
                status=str(r["status"]),
                runner=str(r["runner"]),
                job_id=r["job_id"],
                attempts=int(r["attempts"]),
                progress_k=int(r["progress_k"]),
                progress_n=int(r["progress_n"]),
                last_heartbeat_at=clock.parse(r["last_heartbeat_at"])
                if r["last_heartbeat_at"]
                else None,
            )
            for r in rows
        ]

    def save_batch_result(self, result: ReplayResult) -> None:
        write_json_atomic(self.batch_dir(result.batch_id) / "result.json", result)

    def load_batch_result(self, batch_id: str) -> ReplayResult | None:
        path = self.batch_dir(batch_id) / "result.json"
        return self.load_model(path, ReplayResult) if path.exists() else None

    # -- verdicts -------------------------------------------------------------------------
    def save_verdict(self, verdict: Verdict) -> None:
        write_json_atomic(self.verdict_path(verdict.verdict_id), verdict)
        total = sum(verdict.cost.values())
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO verdicts (verdict_id, edit_id, decision, action, usd_total,
                       created_at, session_hash) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(verdict_id) DO UPDATE SET decision=excluded.decision,
                       action=excluded.action, usd_total=excluded.usd_total""",
                (
                    verdict.verdict_id,
                    verdict.edit_id,
                    verdict.decision,
                    verdict.action,
                    total,
                    clock.iso(verdict.created_at),
                    verdict.session_hash,
                ),
            )

    def load_verdict(self, verdict_id: str) -> Verdict:
        path = self.verdict_path(verdict_id)
        if not path.exists():
            raise NotFoundError(f"verdict {verdict_id} not found")
        return self.load_model(path, Verdict)

    def list_verdicts(
        self, *, session_hash: str | None = None, limit: int = 100, before: str | None = None
    ) -> list[VerdictRow]:
        rows = self.query(
            """SELECT * FROM verdicts WHERE (? IS NULL OR session_hash IS NULL OR session_hash = ?)
                   AND (? IS NULL OR created_at < ?) ORDER BY created_at DESC LIMIT ?""",
            (session_hash, session_hash, before, before, limit),
        )
        return [
            VerdictRow(
                verdict_id=str(r["verdict_id"]),
                edit_id=str(r["edit_id"]),
                decision=str(r["decision"]),
                action=str(r["action"]),
                usd_total=float(r["usd_total"]),
                created_at=clock.parse(str(r["created_at"])),
                session_hash=r["session_hash"],
            )
            for r in rows
        ]

    def acquire_action_lock(self, verdict_id: str) -> None:
        """Per-verdict action lock: BEGIN IMMEDIATE + an ``acting`` flag.

        A flag older than :data:`ACTION_LOCK_TTL_S` (or set before ``acting_at`` existed)
        belongs to a holder that died mid-action and is taken over.
        """
        now = clock.now()
        with self.tx(immediate=True) as conn:
            row = conn.execute(
                "SELECT acting, acting_at FROM verdicts WHERE verdict_id = ?", (verdict_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError(f"verdict {verdict_id} not found")
            if int(row["acting"]) and not _lock_expired(row["acting_at"], now):
                raise ConflictError("another action is in progress", code="action_locked")
            conn.execute(
                "UPDATE verdicts SET acting = 1, acting_at = ? WHERE verdict_id = ?",
                (clock.iso(now), verdict_id),
            )

    def release_action_lock(self, verdict_id: str) -> None:
        with self.tx() as conn:
            conn.execute(
                "UPDATE verdicts SET acting = 0, acting_at = NULL WHERE verdict_id = ?",
                (verdict_id,),
            )

    def clear_action_locks(self) -> int:
        """Release every action lock (orchestrator start: no action survives a restart)."""
        with self.tx() as conn:
            cur = conn.execute("UPDATE verdicts SET acting = 0, acting_at = NULL WHERE acting != 0")
            return int(cur.rowcount)

    # -- simulate sessions ----------------------------------------------------------------
    def upsert_session(self, row: SessionRow) -> None:
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO simulate_sessions (session_hash, scenario, edit_id, verdict_id,
                       status, started_at, reset_at) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(session_hash, scenario) DO UPDATE SET edit_id=excluded.edit_id,
                       verdict_id=excluded.verdict_id, status=excluded.status,
                       started_at=excluded.started_at, reset_at=excluded.reset_at""",
                (
                    row.session_hash,
                    row.scenario,
                    row.edit_id,
                    row.verdict_id,
                    row.status,
                    clock.iso(row.started_at),
                    clock.iso(row.reset_at),
                ),
            )

    def sessions(self, *, status: str | None = None) -> list[SessionRow]:
        rows = self.query(
            "SELECT * FROM simulate_sessions WHERE (? IS NULL OR status = ?) ORDER BY started_at",
            (status, status),
        )
        return [
            SessionRow(
                session_hash=str(r["session_hash"]),
                scenario=str(r["scenario"]),
                edit_id=r["edit_id"],
                verdict_id=r["verdict_id"],
                status=str(r["status"]),
                started_at=clock.parse(str(r["started_at"])),
                reset_at=clock.parse(str(r["reset_at"])),
            )
            for r in rows
        ]
