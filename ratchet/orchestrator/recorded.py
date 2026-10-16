"""Recorded runs: ``ratchet simulate k --record-fixture`` and ``RATCHET_SIMULATE_MODE=replayed``.

A record (``scenarios/recorded/<k>/``) holds ``verdict.json``, ``edit.json``,
``diff.patch``, the pair traces it references (``traces/<run_id>/trace.json``) and its
events with offsets from the first one (``events.json``). Serving it copies the edit and
verdict under fresh ids with ``recorded=True`` and the session hash, then re-emits the
events at their original relative times compressed ×2, so the progress strip still moves.
The verdict appears when its ``verdict.decided`` event does. No model is called; the
record keeps ``simulated_models`` exactly as it was produced.

``import_recorded`` also keeps every record in the store as a *global* verdict (no session,
``recorded=True``, deterministic id per scenario), so a fresh visitor to the public host can
open the recorded verdict before pressing Simulate; the id is on ``GET /scenarios``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.errors import NotFoundError
from ratchet.log import get_logger
from ratchet.models import SkillEdit, Trace, Verdict
from ratchet.orchestrator.context import EditOptions, save_options
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.store import SessionRow, Store
from ratchet.store.base import write_json_atomic
from ratchet.util import new_id

if TYPE_CHECKING:
    from ratchet.orchestrator.simulate import SimulateStart

__all__ = [
    "RecordedEvent",
    "RecordedStart",
    "record_fixture",
    "start_recorded",
    "recorded_dir",
    "import_recorded",
    "recorded_verdict_id",
]

SPEEDUP = 2.0
_log = get_logger("simulate")


class RecordedEvent(BaseModel):
    offset_ms: int
    kind: str
    ref: str
    payload: dict[str, object]


def recorded_dir(settings: Settings, scenario_id: str) -> Path:
    return settings.ratchet_scenarios_dir / "recorded" / scenario_id


# --- recording --------------------------------------------------------------------------------
def recorded_edit_id(scenario_id: str) -> str:
    """Deterministic per scenario so the import is idempotent and the id is linkable."""
    digest = hashlib.sha256(f"recorded:{scenario_id}".encode()).hexdigest()
    return f"edit_{digest[:12]}"


def recorded_verdict_id(store: Store, scenario_id: str) -> str | None:
    """The global recorded verdict for ``scenario_id`` if ``import_recorded`` kept one."""
    verdict_id = verdict_id_for(recorded_edit_id(scenario_id))
    return verdict_id if store.verdict_path(verdict_id).exists() else None


def import_recorded(settings: Settings, store: Store) -> dict[str, str]:
    """Keep every ``scenarios/recorded/<k>/`` in the store as a global recorded verdict.

    Returns ``{scenario_id: verdict_id}`` for the records found; existing imports are left
    alone. Nothing is emitted on the event stream and no session is created.
    """
    kept: dict[str, str] = {}
    root = settings.ratchet_scenarios_dir / "recorded"
    for path in sorted(p for p in root.glob("*/verdict.json") if p.parent.name.isalnum()):
        scenario_id = path.parent.name
        edit_id = recorded_edit_id(scenario_id)
        verdict_id = verdict_id_for(edit_id)
        kept[scenario_id] = verdict_id
        if store.verdict_path(verdict_id).exists():
            continue
        record = _load(path.parent)
        _import_traces(store, path.parent)
        edit = record.edit.model_copy(update={"edit_id": edit_id, "session_hash": None})
        verdict = record.verdict.model_copy(
            update={
                "verdict_id": verdict_id,
                "edit_id": edit_id,
                "session_hash": None,
                "recorded": True,
            }
        )
        store.save_edit(edit, record.diff)
        save_options(store, edit_id, EditOptions(scenario_id=scenario_id, repo_dir=None))
        store.save_verdict(verdict)
        store.set_edit_state(edit_id, "done", verdict_id=verdict_id)
        _log.info("simulate.recorded_imported", scenario=scenario_id, verdict_id=verdict_id)
    return kept


def _run_ids(verdict: Verdict) -> list[str]:
    ids = [p.golden_run_id for p in verdict.pairs] + [p.replay_run_id for p in verdict.pairs]
    return sorted({i for i in ids if i})


def _edit_events(store: Store, edit: SkillEdit, verdict_id: str) -> list[RecordedEvent]:
    refs = {edit.edit_id, verdict_id}
    rows = [
        e
        for e in store.events(session_hash=edit.session_hash, include_global=False, limit=1000)
        if e.ref in refs or e.payload.get("edit_id") == edit.edit_id
    ]
    if not rows:
        return []
    t0 = rows[0].ts
    return [
        RecordedEvent(
            offset_ms=int((e.ts - t0).total_seconds() * 1000),
            kind=e.kind,
            ref=e.ref,
            payload=e.payload,
        )
        for e in rows
    ]


def _portable(store: Store, trace: Trace) -> Trace:
    """Data-dir paths become data-dir-relative, so a record holds no host paths."""
    ref = trace.env.world_ref
    root = store.data_dir.resolve()
    # Relative refs are relative to the working directory, so resolve before comparing.
    if ref and Path(ref).resolve().is_relative_to(root):
        rel = Path(ref).resolve().relative_to(root).as_posix()
        return trace.model_copy(update={"env": trace.env.model_copy(update={"world_ref": rel})})
    return trace


def record_fixture(
    store: Store, settings: Settings, verdict_id: str, scenario_id: str, dest: Path | None = None
) -> Path:
    """Write a finished run under ``scenarios/recorded/<scenario_id>/``; returns the dir."""
    verdict = store.load_verdict(verdict_id)
    edit = store.load_edit(verdict.edit_id)
    out = dest or recorded_dir(settings, scenario_id)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    write_json_atomic(out / "verdict.json", verdict)
    write_json_atomic(out / "edit.json", edit)
    (out / "diff.patch").write_text(store.load_diff(edit.edit_id), encoding="utf-8")
    for run_id in _run_ids(verdict):
        if store.has_trace(run_id):
            trace = _portable(store, store.load_trace(run_id))
            write_json_atomic(out / "traces" / run_id / "trace.json", trace)
    events = [e.model_dump(mode="json") for e in _edit_events(store, edit, verdict_id)]
    write_json_atomic(out / "events.json", events)
    _log.info("simulate.recorded", scenario=scenario_id, n_events=len(events))
    return out


# --- serving ----------------------------------------------------------------------------------
@dataclass(frozen=True)
class _Record:
    verdict: Verdict
    edit: SkillEdit
    diff: str
    events: list[RecordedEvent]


def _load(root: Path) -> _Record:
    if not (root / "verdict.json").is_file():
        raise NotFoundError("no recorded run for this scenario", code="not_found")
    events = json.loads((root / "events.json").read_text(encoding="utf-8"))
    return _Record(
        verdict=Verdict.model_validate_json((root / "verdict.json").read_text(encoding="utf-8")),
        edit=SkillEdit.model_validate_json((root / "edit.json").read_text(encoding="utf-8")),
        diff=(root / "diff.patch").read_text(encoding="utf-8"),
        events=[RecordedEvent.model_validate(e) for e in events],
    )


def _import_traces(store: Store, root: Path) -> None:
    for path in sorted((root / "traces").glob("*/trace.json")):
        trace = Trace.model_validate_json(path.read_text(encoding="utf-8"))
        if store.has_trace(trace.run_id):
            continue
        if trace.kind == "golden":  # readable by id, never part of the shared library
            trace = trace.model_copy(update={"archived_reason": "recorded"})
        store.save_trace(trace)


def _remap(value: object, ids: dict[str, str]) -> object:
    if isinstance(value, str):
        return ids.get(value, value)
    if isinstance(value, list):
        return [_remap(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _remap(v, ids) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class RecordedStart:
    view: SimulateStart
    task: asyncio.Task[None]


async def _replay_events(
    store: Store, record: _Record, edit: SkillEdit, verdict: Verdict, ids: dict[str, str]
) -> None:
    session = edit.session_hash
    loop_t0 = asyncio.get_running_loop().time()
    for event in record.events:
        due = loop_t0 + event.offset_ms / 1000.0 / SPEEDUP
        await asyncio.sleep(max(due - asyncio.get_running_loop().time(), 0.0))
        payload = _remap(event.payload, ids)
        assert isinstance(payload, dict)
        if event.kind == "orchestrator.state":
            await asyncio.to_thread(store.set_edit_state, edit.edit_id, str(payload.get("to")))
        if event.kind == "verdict.decided":
            await asyncio.to_thread(store.save_verdict, verdict)
            await asyncio.to_thread(
                store.set_edit_state, edit.edit_id, "deciding", verdict_id=verdict.verdict_id
            )
        ref = str(_remap(event.ref, ids))
        await asyncio.to_thread(store.emit, event.kind, ref, payload, session_hash=session)
    await asyncio.to_thread(store.save_verdict, verdict)
    await asyncio.to_thread(
        store.set_edit_state, edit.edit_id, "done", verdict_id=verdict.verdict_id
    )
    for row in await asyncio.to_thread(store.sessions):
        if row.edit_id == edit.edit_id:
            await asyncio.to_thread(store.upsert_session, replace(row, status="done"))


def _prepare(
    settings: Settings, store: Store, scenario_id: str, session_hash: str
) -> tuple[_Record, SkillEdit, Verdict, dict[str, str]]:
    root = recorded_dir(settings, scenario_id)
    record = _load(root)
    _import_traces(store, root)
    edit_id = new_id("edit")
    verdict_id = verdict_id_for(edit_id)
    now = clock.now()
    edit = record.edit.model_copy(
        update={"edit_id": edit_id, "session_hash": session_hash, "detected_at": now}
    )
    verdict = record.verdict.model_copy(
        update={
            "verdict_id": verdict_id,
            "edit_id": edit_id,
            "session_hash": session_hash,
            "recorded": True,
            "created_at": now,
        }
    )
    store.save_edit(edit, record.diff)
    save_options(store, edit_id, EditOptions(scenario_id=scenario_id, repo_dir=None))
    reset_at = now + timedelta(seconds=settings.ratchet_simulate_reset_s)
    store.upsert_session(
        SessionRow(session_hash, scenario_id, edit_id, verdict_id, "running", now, reset_at)
    )
    ids = {record.edit.edit_id: edit_id, record.verdict.verdict_id: verdict_id}
    if record.edit.session_hash:
        ids[record.edit.session_hash] = session_hash
    return record, edit, verdict, ids


async def start_recorded(
    settings: Settings, store: Store, scenario_id: str, session_hash: str
) -> RecordedStart:
    """Serve the recorded run for ``scenario_id`` to ``session_hash`` (replayed mode)."""
    from ratchet.orchestrator.simulate import SimulateStart  # noqa: F811 - runtime import

    record, edit, verdict, ids = await asyncio.to_thread(
        _prepare, settings, store, scenario_id, session_hash
    )
    task = asyncio.get_running_loop().create_task(_replay_events(store, record, edit, verdict, ids))
    view = SimulateStart(
        session_hash=session_hash,
        scenario_id=scenario_id,
        edit_id=edit.edit_id,
        verdict_id=verdict.verdict_id,
        status="running",
    )
    return RecordedStart(view=view, task=task)
