"""Manual Rollback / Approve: lock, idempotency, git effects, events."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import commit_edit, make_settings, make_verdict, open_store, seeded_repo  # noqa: E402

from ratchet.config.settings import Settings  # noqa: E402
from ratchet.errors import ConflictError  # noqa: E402
from ratchet.models import Verdict  # noqa: E402
from ratchet.orchestrator.actions import approve, rollback  # noqa: E402
from ratchet.store import Store  # noqa: E402
from ratchet.watcher.gitops import SkillsRepo  # noqa: E402

NOTE = "github-triage/NOTE.md"


def _held(tmp_path: Path, **update: object) -> tuple[Settings, Store, SkillsRepo, Verdict]:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    repo = seeded_repo(settings)
    edit, diff = commit_edit(repo, {NOTE: "held edit\n"})
    store.save_edit(edit, diff)
    verdict = make_verdict(edit, **update)
    store.save_verdict(verdict)
    store.set_edit_state(edit.edit_id, "done", verdict_id=verdict.verdict_id)
    return settings, store, repo, verdict


async def test_rollback_reverts_on_candidate_and_leaves_main_content(tmp_path: Path) -> None:
    settings, store, repo, verdict = _held(tmp_path)
    before = set(repo.changed_files(repo.main_head(), repo.candidate_head()))
    assert before == {NOTE}
    done = await rollback(store, settings, verdict.verdict_id, actor="judge")
    assert done.action == "reverted" and done.rolled_back_by == "judge" and done.action_commit
    assert not (repo.candidate_dir / NOTE).exists() and not (repo.main_dir / NOTE).exists()
    (event,) = store.events(kinds=["verdict.rolled_back"])
    assert event.ref == verdict.verdict_id and event.payload["actor"] == "judge"
    assert event.payload["affected_skills"] == list(verdict.affected_skills)
    assert event.payload["toast"] == "Rolled back. main is unchanged."
    with pytest.raises(ConflictError) as info:  # a second rollback is not a silent success
        await rollback(store, settings, verdict.verdict_id, actor="judge")
    assert info.value.code == "already_reverted"
    assert repo.candidate_head() == done.action_commit
    assert len(store.events(kinds=["verdict.rolled_back"])) == 1


async def test_approve_merges_a_held_edit_once(tmp_path: Path) -> None:
    settings, store, repo, verdict = _held(tmp_path)
    done = await approve(store, settings, verdict.verdict_id, actor="judge")
    assert done.action == "merged" and done.approved_at is not None
    assert (repo.main_dir / NOTE).read_text() == "held edit\n"
    assert (await approve(store, settings, verdict.verdict_id, actor="judge")).action == "merged"
    (event,) = store.events(kinds=["verdict.acted"])
    assert event.payload["affected_skills"] == list(verdict.affected_skills) != []
    assert event.payload["toast"] == f"Merged. {verdict.affected_skills[0]} is live."


async def test_a_concurrent_action_is_locked_out(tmp_path: Path) -> None:
    settings, store, _, verdict = _held(tmp_path)
    store.acquire_action_lock(verdict.verdict_id)
    with pytest.raises(ConflictError) as info:
        await rollback(store, settings, verdict.verdict_id, actor="judge")
    assert info.value.code == "action_locked"
    store.release_action_lock(verdict.verdict_id)
    assert (await rollback(store, settings, verdict.verdict_id, actor="j")).action == "reverted"


async def test_a_recorded_run_changes_only_its_record(tmp_path: Path) -> None:
    settings, store, repo, verdict = _held(tmp_path, recorded=True)
    head = repo.candidate_head()
    done = await rollback(store, settings, verdict.verdict_id, actor="judge")
    assert done.action == "reverted" and done.action_commit is None
    assert repo.candidate_head() == head
