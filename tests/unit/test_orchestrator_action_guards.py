"""Action guards: stacked reverts, rollback of a merged edit, approve anyway,
acting vs user actions, stale action locks."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import commit_edit, make_settings, make_verdict, open_store, seeded_repo  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.config.settings import Settings  # noqa: E402
from ratchet.errors import ConflictError  # noqa: E402
from ratchet.models import SkillEdit, Verdict  # noqa: E402
from ratchet.orchestrator import deciding  # noqa: E402
from ratchet.orchestrator.actions import approve, rollback  # noqa: E402
from ratchet.orchestrator.service import Orchestrator  # noqa: E402
from ratchet.store import Store  # noqa: E402
from ratchet.watcher.gitops import SkillsRepo  # noqa: E402

X_FILE = "github-triage/NOTE.md"
E_FILE = "expense-report/NOTE.md"
F_FILE = "github-triage/LATER.md"


def _setup(tmp_path: Path) -> tuple[Settings, Store, SkillsRepo]:
    settings = make_settings(tmp_path)
    return settings, open_store(settings), seeded_repo(settings)


def _edit(
    store: Store, repo: SkillsRepo, files: dict[str, str | None], **verdict: Any
) -> tuple[SkillEdit, Verdict]:
    edit, diff = commit_edit(repo, files, stacked=True)
    store.save_edit(edit, diff)
    made = make_verdict(edit, **verdict)
    store.save_verdict(made)
    store.set_edit_state(edit.edit_id, "done", verdict_id=made.verdict_id)
    return edit, made


def _ctx(settings: Settings, store: Store, repo: SkillsRepo, edit: SkillEdit) -> Any:
    work = store.edit_dir(edit.edit_id) / "attempt_1"
    work.mkdir(parents=True, exist_ok=True)
    services = SimpleNamespace(settings=settings, store=store)
    return SimpleNamespace(
        services=services,
        settings=settings,
        store=store,
        repo=repo,
        edit=edit,
        edit_id=edit.edit_id,
        work=work,
        session_hash=None,
    )


# --- stacked reverts ----------------------------------------------------------------------
async def test_merging_a_later_edit_never_ships_a_reverted_earlier_one(tmp_path: Path) -> None:
    """commit X, commit E, revert X, merge E → main has E's change and not X's."""
    settings, store, repo = _setup(tmp_path)
    x_edit, x_verdict = _edit(store, repo, {X_FILE: "regression\n"}, decision="BLOCK")
    _, e_verdict = _edit(store, repo, {E_FILE: "good edit\n"})
    await rollback(store, settings, x_verdict.verdict_id, actor="judge")  # revert(X) above E
    done = await approve(store, settings, e_verdict.verdict_id, actor="operator")
    assert done.action == "merged"
    assert (repo.main_dir / E_FILE).read_text() == "good edit\n"
    assert not (repo.main_dir / X_FILE).exists()
    assert repo.is_ancestor(x_edit.commit, repo.main_head())  # carried with its revert


def test_automatic_merge_carries_the_revert_of_a_stacked_block(tmp_path: Path) -> None:
    """Same as above through the acting stage (PASS on E after BLOCK on X)."""
    settings, store, repo = _setup(tmp_path)
    x_edit, _ = _edit(store, repo, {X_FILE: "regression\n"}, decision="BLOCK")
    e_edit, e_verdict = _edit(store, repo, {E_FILE: "good edit\n"}, decision="PASS")
    store.save_verdict(make_verdict(x_edit, decision="BLOCK", action="reverted"))
    deciding._act_locked(_ctx(settings, store, repo, x_edit))  # the revert lands above E
    store.save_verdict(e_verdict.model_copy(update={"action": "merged"}))
    acted = deciding._act_locked(_ctx(settings, store, repo, e_edit))
    assert acted.action == "merged"
    assert (repo.main_dir / E_FILE).read_text() == "good edit\n"
    assert not (repo.main_dir / X_FILE).exists()


def test_acting_on_a_later_edit_while_the_block_is_unreverted_holds_it(tmp_path: Path) -> None:
    """The deciding → acting window: X is decided ``reverted`` but its revert has not run."""
    settings, store, repo = _setup(tmp_path)
    x_edit, _ = _edit(store, repo, {X_FILE: "regression\n"})
    store.save_verdict(make_verdict(x_edit, decision="BLOCK", action="reverted"))
    e_edit, e_verdict = _edit(store, repo, {E_FILE: "good edit\n"}, decision="PASS")
    store.save_verdict(e_verdict.model_copy(update={"action": "merged"}))
    main_before = repo.main_head()
    acted = deciding._act_locked(_ctx(settings, store, repo, e_edit))
    assert acted.action == "held" and "stacked_on_held" in acted.reasons
    assert repo.main_head() == main_before


# --- rollback of a merged edit -------------------------------------------------------------
async def test_rollback_of_a_merged_edit_that_cannot_leave_main_is_refused(
    tmp_path: Path,
) -> None:
    settings, store, repo = _setup(tmp_path)
    _, e_verdict = _edit(store, repo, {E_FILE: "good edit\n"})
    merged = await approve(store, settings, e_verdict.verdict_id, actor="operator")
    _edit(store, repo, {F_FILE: "held later\n"})  # an unjudged edit now sits on candidate
    main, cand = repo.main_head(), repo.candidate_head()
    with pytest.raises(ConflictError) as info:
        await rollback(store, settings, e_verdict.verdict_id, actor="operator")
    assert info.value.code == "stacked_on_held"
    assert (repo.main_head(), repo.candidate_head()) == (main, cand)
    assert store.load_verdict(e_verdict.verdict_id).action == "merged" == merged.action
    assert (repo.main_dir / E_FILE).read_text() == "good edit\n"


async def test_rollback_of_a_merged_edit_removes_it_from_main(tmp_path: Path) -> None:
    settings, store, repo = _setup(tmp_path)
    _, e_verdict = _edit(store, repo, {E_FILE: "good edit\n"})
    await approve(store, settings, e_verdict.verdict_id, actor="operator")
    done = await rollback(store, settings, e_verdict.verdict_id, actor="operator")
    assert done.action == "reverted" and repo.main_head() == done.action_commit
    assert not (repo.main_dir / E_FILE).exists()


# --- approve anyway -----------------------------------------------------------------------
async def _blocked(tmp_path: Path) -> tuple[Settings, Store, SkillsRepo, Verdict]:
    settings, store, repo = _setup(tmp_path)
    edit, verdict = _edit(store, repo, {X_FILE: "blocked edit\n"}, decision="BLOCK")
    revert = repo.revert(edit.commit)
    verdict = verdict.model_copy(update={"action": "reverted", "action_commit": revert})
    store.save_verdict(verdict)
    return settings, store, repo, verdict


async def test_approve_on_a_blocked_verdict_reapplies_and_merges(tmp_path: Path) -> None:
    settings, store, repo, verdict = await _blocked(tmp_path)
    assert not (repo.main_dir / X_FILE).exists()
    done = await approve(store, settings, verdict.verdict_id, actor="operator")
    assert done.action == "merged" and done.approved_at is not None
    assert done.action_commit == repo.main_head()
    assert (repo.main_dir / X_FILE).read_text() == "blocked edit\n"
    (event,) = store.events(kinds=["verdict.acted"])
    assert event.payload["override"] is True and event.payload["toast"]


async def test_approve_after_a_user_rollback_reapplies_and_keeps_the_history(
    tmp_path: Path,
) -> None:
    """Pinned semantics 1: Approve on ANY reverted verdict (here a user rollback of a held
    edit) re-applies the change and merges; rolled_back_* stay for history."""
    settings, store, repo = _setup(tmp_path)
    edit, verdict = _edit(store, repo, {E_FILE: "held edit\n"}, decision="WARN", action="held")
    rolled = await rollback(store, settings, verdict.verdict_id, actor="judge")
    assert rolled.action == "reverted" and not (repo.candidate_dir / E_FILE).exists()
    done = await approve(store, settings, verdict.verdict_id, actor="operator")
    assert done.action == "merged" and done.approved_at is not None
    assert done.rolled_back_by == "judge" and done.rolled_back_at == rolled.rolled_back_at
    assert (repo.main_dir / E_FILE).read_text() == "held edit\n"
    assert done.action_commit == repo.main_head() == repo.candidate_head()
    (event,) = store.events(kinds=["verdict.acted"])
    assert event.payload["override"] is True
    assert event.payload["toast"].startswith("Approved anyway.")


async def test_approve_on_a_merged_verdict_is_idempotent(tmp_path: Path) -> None:
    """Pinned semantics 3: Approve on action=merged answers with the verdict unchanged."""
    settings, store, repo = _setup(tmp_path)
    _, verdict = _edit(store, repo, {E_FILE: "good\n"}, action="merged", action_commit="abc")
    head = repo.main_head()
    again = await approve(store, settings, verdict.verdict_id, actor="operator")
    assert again == store.load_verdict(verdict.verdict_id) == verdict
    assert repo.main_head() == head and not store.events(kinds=["verdict.acted"])


async def test_approve_anyway_respects_the_stacking_guard(tmp_path: Path) -> None:
    settings, store, repo, verdict = await _blocked(tmp_path)
    _edit(store, repo, {F_FILE: "held later\n"})
    cand = repo.candidate_head()
    with pytest.raises(ConflictError) as info:
        await approve(store, settings, verdict.verdict_id, actor="operator")
    assert info.value.code == "stacked_on_held" and repo.candidate_head() == cand
    assert store.load_verdict(verdict.verdict_id).action == "reverted"


async def test_rollback_of_a_blocked_verdict_is_already_reverted(tmp_path: Path) -> None:
    settings, store, repo, verdict = await _blocked(tmp_path)
    cand = repo.candidate_head()
    with pytest.raises(ConflictError) as info:
        await rollback(store, settings, verdict.verdict_id, actor="operator")
    assert info.value.code == "already_reverted" and repo.candidate_head() == cand
    assert info.value.http_status == 409


# --- acting vs user actions ---------------------------------------------------------------
@pytest.mark.parametrize("state", ["deciding", "acting", "notifying"])
async def test_user_actions_wait_for_a_terminal_edit(tmp_path: Path, state: str) -> None:
    settings, store, repo = _setup(tmp_path)
    edit, verdict = _edit(store, repo, {E_FILE: "good edit\n"})
    store.set_edit_state(edit.edit_id, state)
    for action in (approve, rollback):
        with pytest.raises(ConflictError) as info:
            await action(store, settings, verdict.verdict_id, actor="operator")
        assert info.value.code == "not_ready" and info.value.http_status == 409


def test_acting_takes_the_action_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings, store, repo = _setup(tmp_path)
    edit, verdict = _edit(store, repo, {E_FILE: "good edit\n"}, decision="PASS")
    store.save_verdict(verdict.model_copy(update={"action": "merged"}))
    monkeypatch.setattr(deciding, "_LOCK_WAIT_S", 0.2)
    main = repo.main_head()
    store.acquire_action_lock(verdict.verdict_id)  # a user action is in flight
    with pytest.raises(ConflictError) as info:
        deciding._act_locked(_ctx(settings, store, repo, edit))
    assert info.value.code == "action_locked" and repo.main_head() == main
    store.release_action_lock(verdict.verdict_id)
    assert deciding._act_locked(_ctx(settings, store, repo, edit)).action == "merged"
    store.acquire_action_lock(verdict.verdict_id)  # and released it afterwards


def test_acting_rereads_the_verdict_and_keeps_a_user_action(tmp_path: Path) -> None:
    settings, store, repo = _setup(tmp_path)
    edit, verdict = _edit(store, repo, {E_FILE: "good edit\n"}, decision="PASS")
    rolled = verdict.model_copy(
        update={"action": "reverted", "rolled_back_at": clock.now(), "action_commit": "abc"}
    )
    store.save_verdict(rolled)
    main, cand = repo.main_head(), repo.candidate_head()
    acted = deciding._act_locked(_ctx(settings, store, repo, edit))
    assert acted.action == "reverted" and acted.action_commit == "abc"
    assert (repo.main_head(), repo.candidate_head()) == (main, cand)


# --- stale action locks -------------------------------------------------------------------
def test_a_stale_action_lock_expires(tmp_path: Path) -> None:
    _, store, repo = _setup(tmp_path)
    _, verdict = _edit(store, repo, {E_FILE: "x\n"})
    store.acquire_action_lock(verdict.verdict_id)
    with pytest.raises(ConflictError):
        store.acquire_action_lock(verdict.verdict_id)
    with store.tx() as conn:
        conn.execute(
            "UPDATE verdicts SET acting_at = '2000-01-01T00:00:00+00:00' WHERE verdict_id = ?",
            (verdict.verdict_id,),
        )
    store.acquire_action_lock(verdict.verdict_id)  # taken over, no ConflictError


async def test_orchestrator_start_clears_stuck_action_locks(tmp_path: Path) -> None:
    settings, store, repo = _setup(tmp_path)
    _, verdict = _edit(store, repo, {E_FILE: "x\n"})
    store.acquire_action_lock(verdict.verdict_id)  # a crash left the flag set
    orch = Orchestrator(settings, store, digest_enabled=False)
    await orch.start(watch=False)
    try:
        store.acquire_action_lock(verdict.verdict_id)
    finally:
        await orch.stop()
