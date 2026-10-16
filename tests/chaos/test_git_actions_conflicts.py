"""A git merge conflict surfaces as ConflictError(merge_conflict); nothing moves."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.errors import ConflictError
from ratchet.watcher._git import run_git
from ratchet.watcher.gitops import SkillsRepo

SKILL = "---\nname: expense-report\ndescription: Build expense reports.\n---\nSum receipts.\n"


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    seed = tmp_path / "seed" / "expense-report"
    seed.mkdir(parents=True)
    (seed / "SKILL.md").write_text(SKILL)
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(tmp_path / "seed")
    return skills


def commit_directly_on_main(repo: SkillsRepo, text: str) -> str:
    """Someone bypassed Ratchet and committed to main (the divergence the guard catches)."""
    (repo.main_dir / "expense-report" / "SKILL.md").write_text(text)
    run_git(repo.main_dir, "commit", "-q", "-am", "manual commit on main")
    return repo.main_head()


def test_diverged_main_raises_merge_conflict_and_leaves_both_branches(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(SKILL + "Add VAT.\n")
    candidate = repo.commit_candidate("edit", author="agent")
    main = commit_directly_on_main(repo, SKILL + "Drop VAT.\n")

    with pytest.raises(ConflictError) as info:
        repo.merge_candidate("verdict_conflict")

    assert info.value.code == "merge_conflict"
    assert info.value.http_status == 409
    assert repo.main_head() == main
    assert repo.candidate_head() == candidate
    assert (repo.main_dir / "expense-report" / "SKILL.md").read_text() == SKILL + "Drop VAT.\n"
    assert repo.dirty_paths("main") == []


def test_non_conflicting_divergence_is_still_refused(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "new-skill.md").write_text("x\n")
    repo.commit_candidate("edit", author="user")
    commit_directly_on_main(repo, SKILL + "unrelated\n")
    with pytest.raises(ConflictError) as info:
        repo.merge_candidate("verdict_nonff")
    assert info.value.code == "merge_conflict"


def test_conflicting_revert_is_aborted_cleanly(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(SKILL + "one\n")
    first = repo.commit_candidate("first", author="agent")
    assert first is not None
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(SKILL + "one\ntwo\n")
    second = repo.commit_candidate("second", author="agent")

    with pytest.raises(ConflictError) as info:
        repo.revert(first)

    assert info.value.code == "merge_conflict"
    assert repo.candidate_head() == second
    assert repo.dirty_paths("candidate") == []
    assert not (repo.main_dir / ".git" / "worktrees" / "skills-candidate" / "REVERT_HEAD").exists()
