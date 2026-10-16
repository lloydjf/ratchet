"""Stacked edits on candidate: a merge never ships an earlier edit that is still held."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import commit_edit, make_settings, make_verdict, open_store, seeded_repo  # noqa: E402

from ratchet.config.settings import Settings  # noqa: E402
from ratchet.errors import ConflictError  # noqa: E402
from ratchet.models import SkillEdit  # noqa: E402
from ratchet.orchestrator.actions import approve, rollback  # noqa: E402
from ratchet.orchestrator.stacking import STACKED_ON_HELD, unresolved_ancestors  # noqa: E402
from ratchet.store import Store  # noqa: E402
from ratchet.watcher.gitops import SkillsRepo  # noqa: E402

FIRST = "github-triage/NOTE.md"
SECOND = "expense-report/NOTE.md"


def _two_stacked(tmp_path: Path) -> tuple[Settings, Store, SkillsRepo, SkillEdit, SkillEdit]:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    repo = seeded_repo(settings)
    first, diff1 = commit_edit(repo, {FIRST: "held edit\n"})
    store.save_edit(first, diff1)
    second, diff2 = commit_edit(repo, {SECOND: "later edit\n"}, stacked=True)
    store.save_edit(second, diff2)
    for edit in (first, second):
        store.set_edit_state(edit.edit_id, "done")
    return settings, store, repo, first, second


def test_held_earlier_edit_blocks_a_later_merge(tmp_path: Path) -> None:
    _, store, repo, first, second = _two_stacked(tmp_path)
    store.save_verdict(make_verdict(first))  # WARN, held
    assert unresolved_ancestors(store, repo, second) == [first.edit_id]


def test_edit_still_being_judged_blocks_a_later_merge(tmp_path: Path) -> None:
    _, store, repo, first, second = _two_stacked(tmp_path)
    assert unresolved_ancestors(store, repo, second) == [first.edit_id]


async def test_reverted_earlier_edit_blocks_when_its_revert_sits_above(tmp_path: Path) -> None:
    """X, E, revert(X) — merging E alone would ship X; only merging up to the revert
    (net zero for X) is safe."""
    settings, store, repo, first, second = _two_stacked(tmp_path)
    held = make_verdict(first)
    store.save_verdict(held)
    await rollback(store, settings, held.verdict_id, actor="operator")
    assert unresolved_ancestors(store, repo, second) == [first.edit_id]
    assert unresolved_ancestors(store, repo, second, target=repo.candidate_head()) == []


def test_reverted_earlier_edit_below_the_target_does_not_block(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    repo = seeded_repo(settings)
    main_before = repo.main_head()
    first, diff1 = commit_edit(repo, {FIRST: "blocked edit\n"})
    store.save_edit(first, diff1)
    repo.apply_files({"other.md": "not an edit\n"}, message="other")  # keeps main behind
    revert = repo.revert(first.commit)  # X, other, revert(X)
    assert repo.main_head() == main_before
    store.save_verdict(
        make_verdict(first, decision="BLOCK", action="reverted", action_commit=revert)
    )
    second, diff2 = commit_edit(repo, {SECOND: "later edit\n"}, stacked=True)
    store.save_edit(second, diff2)
    assert unresolved_ancestors(store, repo, second) == []


def test_decided_reverted_but_not_yet_acted_blocks(tmp_path: Path) -> None:
    """The verdict is saved as ``reverted`` at deciding, before the revert runs."""
    _, store, repo, first, second = _two_stacked(tmp_path)
    store.save_verdict(make_verdict(first, decision="BLOCK", action="reverted"))
    assert unresolved_ancestors(store, repo, second) == [first.edit_id]


def test_first_edit_has_no_unresolved_ancestors(tmp_path: Path) -> None:
    _, store, repo, first, _ = _two_stacked(tmp_path)
    assert unresolved_ancestors(store, repo, first) == []


async def test_approving_a_later_edit_over_a_held_one_is_refused(tmp_path: Path) -> None:
    settings, store, repo, first, second = _two_stacked(tmp_path)
    store.save_verdict(make_verdict(first))
    later = make_verdict(second)
    store.save_verdict(later)
    main_before = repo.main_head()
    with pytest.raises(ConflictError) as info:
        await approve(store, settings, later.verdict_id, actor="operator")
    assert info.value.code == STACKED_ON_HELD
    assert repo.main_head() == main_before


async def test_approving_the_earlier_edit_first_then_the_later_one_works(tmp_path: Path) -> None:
    settings, store, repo, first, second = _two_stacked(tmp_path)
    earlier, later = make_verdict(first), make_verdict(second)
    store.save_verdict(earlier)
    store.save_verdict(later)
    done = await approve(store, settings, earlier.verdict_id, actor="operator")
    assert done.action == "merged"
    done = await approve(store, settings, later.verdict_id, actor="operator")
    assert done.action == "merged"
    assert repo.main_head() == done.action_commit
