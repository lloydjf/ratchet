"""Bless, rebless, unbless and success criteria (the bless and rebless pipelines).

- ``bless``: a ``live`` trace (a run an external harness left through the proxy, with step
  files but no ``trace.json``, is first finalized by ``capture.external``) → PII pass over
  ``task_input``/``final_output`` (token map saved) → ``judge.bless.enrich`` (Nano: world
  facts, side-effect classes, criteria) → ``kind="golden"`` → ``trace.blessed``.
- ``rebless``: the replay becomes the golden (new run id, the old golden's ``env``, facts
  and criteria re-derived from the new output); overlay cassettes the replay was served
  are promoted into the main cassette store; the old golden is archived with
  ``archived_reason="drift"`` and the pair's drift checks (``archived_drift.json`` next to
  its ``trace.json``); ``pair.reblessed``. Inside a Simulate session the new golden
  is a session-scoped copy (``archived_reason="session:<hash>"``, so it never joins the
  shared library) and the old golden is left alone.
- Public mode refuses everything here with ``ForbiddenError(not_public)`` except the
  session-scoped rebless.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from ratchet import clock
from ratchet.capture.external import captured_runs, finalize_captured, is_captured
from ratchet.config.settings import Settings
from ratchet.errors import ConflictError, ForbiddenError, GitError, NotFoundError
from ratchet.judge.bless import enrich
from ratchet.judge.structured import ChatClient
from ratchet.llm.client import ModelClient
from ratchet.models import DriftCheck, ToolCall, Trace
from ratchet.orchestrator.context import InProcessProxy
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.pii import PiiTokenizer
from ratchet.store import Store
from ratchet.store.base import write_json_atomic
from ratchet.util import new_id
from ratchet.watcher.gitops import SkillsRepo

__all__ = [
    "bless",
    "rebless",
    "unbless",
    "set_criteria",
    "promote_overlay",
    "pair_drift",
    "ARCHIVED_DRIFT_FILE",
    "ensure_live_trace",
    "last_unblessed",
]

ARCHIVED_DRIFT_FILE = "archived_drift.json"


def _refuse_public(settings: Settings, what: str) -> None:
    if settings.ratchet_public_mode:
        raise ForbiddenError(f"{what} is not available on the demo", code="not_public")


async def _with_client(
    store: Store, settings: Settings, run_id: str, client: ChatClient | None
) -> tuple[ChatClient, InProcessProxy | None]:
    if client is not None:
        return client, None
    proxy = InProcessProxy(settings, store)
    return ModelClient(run_id=run_id, settings=settings, http=proxy.client), proxy


def _tokenize(settings: Settings, trace: Trace) -> Trace:
    pii = PiiTokenizer.for_settings(settings)
    update = {
        "task_input": pii.tokenize_text(trace.task_input),
        "final_output": pii.tokenize_text(trace.final_output),
    }
    pii.save()
    return trace.model_copy(update=update)


def _skills_commit(settings: Settings) -> str:
    try:
        return SkillsRepo(settings.ratchet_skills_repo).main_head()
    except (GitError, OSError):
        return "unknown"


def ensure_live_trace(store: Store, settings: Settings, run_id: str) -> Trace:
    """The run's trace; a proxy-captured run without ``trace.json`` is finalized first."""
    if not store.has_trace(run_id) and is_captured(store, run_id):
        return finalize_captured(store, run_id, skills_commit=_skills_commit(settings))
    return store.load_trace(run_id)


def last_unblessed(store: Store) -> str | None:
    """``ratchet bless --last``: the newest live trace or captured run, by activity time."""
    candidates: list[tuple[str, str]] = []
    rows = store.list_traces(kind="live", limit=1)
    if rows:
        candidates.append((clock.iso(rows[0].created_at), rows[0].run_id))
    runs = captured_runs(store, limit=1)
    if runs:
        candidates.append((clock.iso(runs[0].last_step_at), runs[0].run_id))
    return max(candidates)[1] if candidates else None


async def bless(
    store: Store, settings: Settings, run_id: str, *, client: ChatClient | None = None
) -> Trace:
    """Make a live run golden; idempotent for a run that is already golden."""
    _refuse_public(settings, "Blessing")
    trace = await asyncio.to_thread(ensure_live_trace, store, settings, run_id)
    if trace.kind == "golden":
        return trace
    if trace.kind != "live":
        raise ConflictError("only live runs can be blessed", code="conflict")
    model, proxy = await _with_client(store, settings, f"bless_{run_id}", client)
    try:
        clean = await asyncio.to_thread(_tokenize, settings, trace)
        enriched = await enrich(clean, model, store)
    finally:
        if proxy is not None:
            await proxy.aclose()
    golden = enriched.model_copy(update={"kind": "golden", "blessed_at": clock.now()})
    await asyncio.to_thread(store.save_trace, golden)
    payload: dict[str, object] = {
        "run_id": run_id,
        "skills_invoked": golden.skills_invoked,
        "world_dependent": golden.world_dependent,
        "n_facts": len(golden.world_facts),
        "task_label": golden.task_label,
    }
    await asyncio.to_thread(store.emit, "trace.blessed", run_id, payload)
    return golden


def promote_overlay(store: Store, replay: Trace) -> int:
    """Copy the overlay cassettes ``replay`` was served into the main cassette store."""
    root = Path(replay.env.world_ref) if replay.env.world_ref else None
    if root is None or not root.is_dir():
        return 0
    promoted = 0
    for step in replay.steps:
        if isinstance(step, ToolCall) and step.cassette_key:
            src = store.cassette_path(step.tool, step.cassette_key, root)
            if src.is_file():
                dst = store.cassette_path(step.tool, step.cassette_key)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                promoted += 1
    return promoted


def _copy_replay(store: Store, replay: Trace, new_run_id: str) -> None:
    src, dst = store.trace_dir(replay.run_id), store.trace_dir(new_run_id)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("trace.json"))


def pair_drift(
    store: Store, golden_run_id: str, replay_run_id: str
) -> tuple[str | None, list[DriftCheck]]:
    """``(verdict_id, drift checks)`` of the newest verdict that judged this pair."""
    rows = store.query(
        "SELECT edit_id FROM edit_traces WHERE run_id = ? ORDER BY rowid DESC", (golden_run_id,)
    )
    verdict_ids = [verdict_id_for(str(r["edit_id"])) for r in rows]
    verdict_ids += [v.verdict_id for v in store.list_verdicts(limit=200)]
    for verdict_id in dict.fromkeys(verdict_ids):
        try:
            verdict = store.load_verdict(verdict_id)
        except NotFoundError:
            continue
        for pair in verdict.pairs:
            if (pair.golden_run_id, pair.replay_run_id) == (golden_run_id, replay_run_id):
                return verdict.verdict_id, list(pair.drift)
    return None, []


def _archive_drift(
    store: Store, golden_run_id: str, replay_run_id: str, new_run_id: str
) -> list[DriftCheck]:
    verdict_id, checks = pair_drift(store, golden_run_id, replay_run_id)
    record: dict[str, object] = {
        "golden": golden_run_id,
        "replay": replay_run_id,
        "new_golden": new_run_id,
        "verdict_id": verdict_id,
        "archived_at": clock.iso(clock.now()),
        "drift": [c.model_dump(mode="json") for c in checks],
    }
    write_json_atomic(store.trace_dir(golden_run_id) / ARCHIVED_DRIFT_FILE, record)
    return checks


def _check_pair(golden: Trace, replay: Trace) -> None:
    if golden.kind != "golden":
        raise ConflictError("the first run is not a golden", code="conflict")
    if replay.kind != "replay" or replay.golden_of != golden.run_id:
        raise ConflictError("the replay does not belong to this golden", code="conflict")


async def rebless(
    store: Store,
    settings: Settings,
    golden_run_id: str,
    replay_run_id: str,
    *,
    session_hash: str | None = None,
    client: ChatClient | None = None,
) -> tuple[str, str]:
    """``(new_golden_run_id, archived_run_id)``."""
    if session_hash is None:
        _refuse_public(settings, "Re-blessing the shared library")
    golden = await asyncio.to_thread(store.load_trace, golden_run_id)
    replay = await asyncio.to_thread(store.load_trace, replay_run_id)
    _check_pair(golden, replay)
    new_run_id = new_id("run")
    await asyncio.to_thread(_copy_replay, store, replay, new_run_id)
    base = replay.model_copy(
        update={
            "run_id": new_run_id,
            "kind": "golden",
            "golden_of": None,
            "env": golden.env,
            "world_facts": [],
            "success_criteria": [],
            "blessed_at": None,
            "task_label": golden.task_label or replay.task_label,
            "archived_reason": f"session:{session_hash}" if session_hash else None,
        }
    )
    model, proxy = await _with_client(store, settings, f"bless_{new_run_id}", client)
    try:
        enriched = await enrich(base, model, store)
    finally:
        if proxy is not None:
            await proxy.aclose()
    new_golden = enriched.model_copy(update={"blessed_at": clock.now(), "kind": "golden"})
    await asyncio.to_thread(store.save_trace, new_golden)
    if session_hash is None:
        await asyncio.to_thread(promote_overlay, store, replay)
        archived = golden.model_copy(update={"archived_reason": "drift"})
        await asyncio.to_thread(store.save_trace, archived)
        checks = await asyncio.to_thread(
            _archive_drift, store, golden_run_id, replay_run_id, new_run_id
        )
    else:
        checks = []
    payload: dict[str, object] = {
        "golden": golden_run_id,
        "replay": replay_run_id,
        "new_golden": new_run_id,
        "archived": golden_run_id,
        "session_scoped": session_hash is not None,
        "drift_fact_ids": [c.fact_id for c in checks],
    }
    await asyncio.to_thread(
        store.emit, "pair.reblessed", golden_run_id, payload, session_hash=session_hash
    )
    return new_run_id, golden_run_id


def unbless(store: Store, settings: Settings, run_id: str) -> Trace:
    """Return a golden to the live runs (it stops being replayed)."""
    _refuse_public(settings, "Unblessing")
    trace = store.load_trace(run_id)
    if trace.kind != "golden":
        raise ConflictError("only goldens can be unblessed", code="conflict")
    live = trace.model_copy(update={"kind": "live", "blessed_at": None})
    store.save_trace(live)
    store.emit("trace.unblessed", run_id, {"run_id": run_id})
    return live


def set_criteria(store: Store, settings: Settings, run_id: str, criteria: list[str]) -> Trace:
    """Replace a golden's success criteria (the judges read them on every replay)."""
    _refuse_public(settings, "Editing criteria")
    trace = store.load_trace(run_id)
    if trace.kind != "golden":
        raise ConflictError("criteria belong to goldens", code="conflict")
    cleaned = [c.strip() for c in criteria if c.strip()]
    updated = trace.model_copy(update={"success_criteria": cleaned})
    store.save_trace(updated)
    store.emit("trace.criteria_set", run_id, {"run_id": run_id, "n_criteria": len(cleaned)})
    return updated
