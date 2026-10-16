"""The progress strip derived from an edit's events."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import T0, commit_edit, make_settings, open_store, seeded_repo  # noqa: E402

from ratchet import clock, copy  # noqa: E402
from ratchet.orchestrator.progress import STALL_AFTER_S, progress_for  # noqa: E402
from ratchet.store import Store  # noqa: E402


def _edit(tmp_path: Path) -> tuple[Store, str]:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    edit, diff = commit_edit(seeded_repo(settings), {"github-triage/NOTE.md": "x\n"})
    with clock.frozen(T0):
        store.save_edit(edit.model_copy(update={"detected_at": T0}), diff)
    return store, edit.edit_id


def _emit(store: Store, edit_id: str, kind: str, at_s: float, **payload: object) -> None:
    with clock.frozen(T0 + timedelta(seconds=at_s)):
        store.emit(kind, edit_id, {"edit_id": edit_id, **payload})


def test_replaying_progress_sums_the_latest_count_per_batch(tmp_path: Path) -> None:
    store, edit_id = _edit(tmp_path)
    _emit(store, edit_id, "edit.selected", 1, n_traces=14)
    _emit(store, edit_id, "orchestrator.state", 1, **{"from": "selecting", "to": "dispatching"})
    _emit(store, edit_id, "batch.submitted", 2, batch_id="b1", runner="local-process", k=0, n=10)
    _emit(store, edit_id, "batch.submitted", 2, batch_id="b2", runner="local-process", k=0, n=4)
    _emit(store, edit_id, "batch.heartbeat", 5, batch_id="b1", runner="local-process", k=3, n=10)
    _emit(store, edit_id, "batch.heartbeat", 6, batch_id="b2", runner="local-process", k=2, n=4)
    with clock.frozen(T0 + timedelta(seconds=6)):
        store.set_edit_state(edit_id, "collecting")
        progress = progress_for(store, edit_id)
    assert progress is not None
    assert (progress.stage, progress.k, progress.n) == ("replaying", 5, 14)
    assert progress.stage_label == "Replaying on the local runner (5/14)"
    assert progress.elapsed_s == 6.0 and not progress.stalled
    statuses = {s.key: s.status for s in progress.stages}
    assert [s.key for s in progress.stages] == list(copy.STAGE_KEYS)
    assert statuses["detected"] == "done" and statuses["selecting"] == "done"
    assert statuses["replaying"] == "active" and statuses["nano"] == "pending"


def test_judging_shows_the_deepest_tier_and_skips_unused_ones(tmp_path: Path) -> None:
    store, edit_id = _edit(tmp_path)
    _emit(store, edit_id, "batch.finished", 1, batch_id="b1", runner="nebius", k=4, n=4)
    _emit(store, edit_id, "pair.level0", 2, hard_fail=False)
    _emit(store, edit_id, "pair.opinion", 3, tier="nano")
    _emit(store, edit_id, "pair.opinion", 3, tier="ultra")
    with clock.frozen(T0 + timedelta(seconds=3)):
        store.set_edit_state(edit_id, "judging")
        progress = progress_for(store, edit_id)
    assert progress is not None and progress.stage == "ultra"
    statuses = {s.key: s.status for s in progress.stages}
    assert statuses["super"] == "skipped" and statuses["nano"] == "done"
    assert statuses["level0"] == "done" and statuses["drift"] == "pending"
    assert "Nebius Jobs" in next(s.label for s in progress.stages if s.key == "replaying")


def test_deciding_is_the_summary_stage(tmp_path: Path) -> None:
    store, edit_id = _edit(tmp_path)
    store.set_edit_state(edit_id, "deciding")
    progress = progress_for(store, edit_id)
    assert progress is not None and progress.stage == "summary"
    assert progress.stage_label == "Writing the summary"


def test_nothing_moving_for_twenty_seconds_is_stalled(tmp_path: Path) -> None:
    store, edit_id = _edit(tmp_path)
    with clock.frozen(T0):
        store.set_edit_state(edit_id, "collecting")
    with clock.frozen(T0 + timedelta(seconds=STALL_AFTER_S + 1)):
        progress = progress_for(store, edit_id)
    assert progress is not None and progress.stalled


def test_a_finished_edit_has_no_progress(tmp_path: Path) -> None:
    store, edit_id = _edit(tmp_path)
    store.set_edit_state(edit_id, "done")
    assert progress_for(store, edit_id) is None
    store.set_edit_state(edit_id, "failed_infra")
    assert progress_for(store, edit_id) is None
