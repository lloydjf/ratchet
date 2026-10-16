"""Merge fast-forwards main; revert restores file content byte-for-byte."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.watcher._git import run_git
from ratchet.watcher.gitops import SkillsRepo

# Bytes that a lossy round trip would change: CRLF, trailing spaces, no final newline,
# non-ASCII, a NUL in a resource file.
ORIGINAL = b"---\r\nname: github-triage\r\ndescription: Triage issues.  \r\n---\r\nBody \xc3\xa9"
RESOURCE = b"\x00\x01binary\xffresource"


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    seed = tmp_path / "seed"
    (seed / "github-triage" / "resources").mkdir(parents=True)
    (seed / "github-triage" / "SKILL.md").write_bytes(ORIGINAL)
    (seed / "github-triage" / "resources" / "blob.bin").write_bytes(RESOURCE)
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(seed)
    return skills


def edit_candidate(repo: SkillsRepo) -> str:
    (repo.candidate_dir / "github-triage" / "SKILL.md").write_bytes(ORIGINAL + b"\nClose all.\n")
    (repo.candidate_dir / "github-triage" / "resources" / "blob.bin").unlink()
    commit = repo.commit_candidate("ratchet: skill edit", author="agent")
    assert commit is not None
    return commit


def history(repo: SkillsRepo, ref: str) -> list[str]:
    return run_git(repo.main_dir, "rev-list", ref).out.split()


def test_merge_fast_forwards_main_and_updates_the_harness_checkout(repo: SkillsRepo) -> None:
    before = repo.main_head()
    commit = edit_candidate(repo)
    merged = repo.merge_candidate("verdict_pass01")
    assert repo.main_head() == merged
    # fast-forward: the old main and the edit are both ancestors; no merge commit parents
    assert repo.is_ancestor(before, merged) and repo.is_ancestor(commit, merged)
    parents = run_git(repo.main_dir, "rev-list", "--parents", "-n", "1", merged).out.split()
    assert len(parents) == 2
    assert history(repo, "main")[1:] == history(repo, commit)
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_bytes() == ORIGINAL + (
        b"\nClose all.\n"
    )
    assert not (repo.main_dir / "github-triage" / "resources" / "blob.bin").exists()
    assert repo.dirty_paths("main") == []


def test_revert_of_a_blocked_edit_keeps_main_tree_unchanged(repo: SkillsRepo) -> None:
    main_before = repo.main_head()
    tree_before = repo.head_tree_hash("main")
    commit = edit_candidate(repo)

    reverted = repo.revert(commit)

    assert repo.candidate_head() == reverted
    assert repo.main_head() == reverted  # main fast-forwarded through edit + revert
    assert repo.is_ancestor(main_before, reverted)
    assert repo.head_tree_hash("main") == tree_before
    skill = repo.candidate_dir / "github-triage"
    assert (skill / "SKILL.md").read_bytes() == ORIGINAL
    assert (skill / "resources" / "blob.bin").read_bytes() == RESOURCE
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_bytes() == ORIGINAL
    assert (repo.main_dir / "github-triage" / "resources" / "blob.bin").read_bytes() == RESOURCE
    subject = run_git(repo.main_dir, "log", "-1", "--format=%s", reverted).out
    assert subject.startswith('Revert "ratchet: skill edit"')


def test_rollback_after_merge_restores_previous_bytes_on_main(repo: SkillsRepo) -> None:
    tree_before = repo.head_tree_hash("main")
    commit = edit_candidate(repo)
    merged = repo.merge_candidate("verdict_pass02")
    history_before_revert = history(repo, "main")

    reverted = repo.revert(commit)

    assert repo.main_head() == reverted
    assert repo.head_tree_hash("main") == tree_before
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_bytes() == ORIGINAL
    assert (repo.main_dir / "github-triage" / "resources" / "blob.bin").read_bytes() == RESOURCE
    # history is only appended to, never rewritten
    assert history(repo, "main")[1:] == history_before_revert
    assert history_before_revert[0] == merged


def test_revert_does_not_carry_unjudged_later_edits_into_main(repo: SkillsRepo) -> None:
    main_before = repo.main_head()
    commit = edit_candidate(repo)
    (repo.candidate_dir / "other.md").write_bytes(b"pending edit\n")
    later = repo.commit_candidate("later", author="user")
    assert later is not None

    reverted = repo.revert(commit)

    assert repo.main_head() == main_before
    assert repo.candidate_head() == reverted
    assert (repo.candidate_dir / "other.md").exists()
    assert (repo.candidate_dir / "github-triage" / "SKILL.md").read_bytes() == ORIGINAL
