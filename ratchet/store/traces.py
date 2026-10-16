"""Trace IO: in-flight step recording, finalized traces, the golden library."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from ratchet import clock
from ratchet.errors import NotFoundError
from ratchet.models import LLMCall, Step, ToolCall, Trace
from ratchet.store.base import StoreBase, write_json_atomic

__all__ = ["TraceStore", "TraceRow"]

_STEP_ADAPTER: TypeAdapter[Step] = TypeAdapter(Step)
_STEP_FILES = {"m": "steps.llm.jsonl", "t": "steps.tool.jsonl"}
_lock = threading.Lock()


@dataclass(frozen=True)
class TraceRow:
    run_id: str
    kind: str
    golden_of: str | None
    world_dependent: bool
    agent_tier: str
    created_at: datetime
    blessed_at: datetime | None
    archived_reason: str | None
    task_label: str
    n_facts: int
    last_replayed_at: datetime | None
    skills: tuple[str, ...]


class TraceStore(StoreBase):
    # -- in-flight steps (proxy writes m*, mock tools / adapter write t*) -------------------
    def _steps_file(self, run_id: str, prefix: str) -> Path:
        return self.trace_dir(run_id) / _STEP_FILES[prefix]

    def next_step_id(self, run_id: str, prefix: str) -> str:
        """``m001``/``t001``… unique within a run; one file per source avoids write races."""
        if prefix not in _STEP_FILES:
            raise ValueError(f"unknown step prefix {prefix!r}")
        path = self._steps_file(run_id, prefix)
        with _lock:
            count = 0
            if path.exists():
                with path.open(encoding="utf-8") as fh:
                    count = sum(1 for _ in fh)
            reserved = path.with_suffix(".next")
            nxt = max(count, int(reserved.read_text()) if reserved.exists() else 0) + 1
            reserved.parent.mkdir(parents=True, exist_ok=True)
            reserved.write_text(str(nxt))
        return f"{prefix}{nxt:03d}"

    def write_step_payload(self, run_id: str, step_id: str, suffix: str, payload: object) -> str:
        """Full request/response bodies: ``steps/<step_id>.req.json`` / ``.res.json``."""
        rel = Path("steps") / f"{step_id}.{suffix}.json"
        write_json_atomic(self.trace_dir(run_id) / rel, payload)
        return rel.as_posix()

    def read_step_payload(self, run_id: str, ref: str) -> object:
        path = (self.trace_dir(run_id) / ref).resolve()
        if not path.is_relative_to(self.trace_dir(run_id).resolve()):
            raise ValueError("step ref escapes the trace directory")
        return json.loads(path.read_text(encoding="utf-8"))

    def append_step(self, run_id: str, step: LLMCall | ToolCall) -> None:
        prefix = "m" if isinstance(step, LLMCall) else "t"
        path = self._steps_file(run_id, prefix)
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write(step.model_dump_json() + "\n")

    def collect_steps(self, run_id: str) -> list[Step]:
        """Merge model and tool steps in arrival order."""
        steps: list[Step] = []
        for name in _STEP_FILES.values():
            path = self.trace_dir(run_id) / name
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        steps.append(_STEP_ADAPTER.validate_json(line))

        def order(step: Step) -> tuple[datetime, str]:
            started = (
                step.started_at
                if step.started_at is not None
                else clock.parse("1970-01-01T00:00:00+00:00")
            )
            return started, step.step_id

        return sorted(steps, key=order)

    # -- finalized traces ---------------------------------------------------------------
    def save_trace(self, trace: Trace) -> None:
        write_json_atomic(self.trace_dir(trace.run_id) / "trace.json", trace)
        with self.tx() as conn:
            conn.execute(
                """INSERT INTO traces (run_id, kind, golden_of, candidate_commit, world_dependent,
                       agent_tier, created_at, blessed_at, archived_reason, task_label, n_facts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(run_id) DO UPDATE SET kind=excluded.kind,
                       golden_of=excluded.golden_of, candidate_commit=excluded.candidate_commit,
                       world_dependent=excluded.world_dependent, blessed_at=excluded.blessed_at,
                       archived_reason=excluded.archived_reason, task_label=excluded.task_label,
                       n_facts=excluded.n_facts""",
                (
                    trace.run_id,
                    trace.kind,
                    trace.golden_of,
                    trace.candidate_commit,
                    int(trace.world_dependent),
                    trace.agent_tier,
                    clock.iso(trace.created_at),
                    clock.iso(trace.blessed_at) if trace.blessed_at else None,
                    trace.archived_reason,
                    trace.task_label,
                    len(trace.world_facts),
                ),
            )
            conn.execute("DELETE FROM trace_skills WHERE run_id = ?", (trace.run_id,))
            conn.executemany(
                "INSERT INTO trace_skills (run_id, skill) VALUES (?, ?)",
                [(trace.run_id, skill) for skill in sorted(set(trace.skills_invoked))],
            )

    def load_trace(self, run_id: str) -> Trace:
        path = self.trace_dir(run_id) / "trace.json"
        if not path.exists():
            raise NotFoundError(f"trace {run_id} not found", code="not_found")
        return self.load_model(path, Trace)

    def has_trace(self, run_id: str) -> bool:
        return (self.trace_dir(run_id) / "trace.json").exists()

    def touch_replayed(self, run_ids: list[str]) -> None:
        stamp = clock.iso(clock.now())
        with self.tx() as conn:
            conn.executemany(
                "UPDATE traces SET last_replayed_at = ? WHERE run_id = ?",
                [(stamp, run_id) for run_id in run_ids],
            )

    def list_traces(
        self,
        *,
        kind: str | None = "golden",
        include_archived: bool = False,
        skill: str | None = None,
        search: str | None = None,
        limit: int = 500,
    ) -> list[TraceRow]:
        clauses: list[str] = []
        params: list[object] = []
        if kind is not None:
            clauses.append("t.kind = ?")
            params.append(kind)
        if not include_archived:
            clauses.append("t.archived_reason IS NULL")
        if skill is not None:
            clauses.append("t.run_id IN (SELECT run_id FROM trace_skills WHERE skill = ?)")
            params.append(skill)
        if search:
            clauses.append("t.task_label LIKE ?")
            params.append(f"%{search}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self.query(
            f"""SELECT t.*, (SELECT group_concat(skill, ',') FROM trace_skills s
                             WHERE s.run_id = t.run_id) AS skills
                FROM traces t {where} ORDER BY t.created_at DESC, t.run_id LIMIT ?""",
            (*params, limit),
        )
        return [_row_to_trace(row) for row in rows]


def _opt_time(value: object) -> datetime | None:
    return clock.parse(str(value)) if value else None


def _row_to_trace(row: sqlite3.Row) -> TraceRow:
    get = row.__getitem__
    skills = str(get("skills") or "")
    return TraceRow(
        run_id=str(get("run_id")),
        kind=str(get("kind")),
        golden_of=get("golden_of"),
        world_dependent=bool(get("world_dependent")),
        agent_tier=str(get("agent_tier")),
        created_at=clock.parse(str(get("created_at"))),
        blessed_at=_opt_time(get("blessed_at")),
        archived_reason=get("archived_reason"),
        task_label=str(get("task_label")),
        n_facts=int(get("n_facts")),
        last_replayed_at=_opt_time(get("last_replayed_at")),
        skills=tuple(sorted(s for s in skills.split(",") if s)),
    )
