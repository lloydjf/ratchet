"""SkillsRepo: init, candidate commits, capture of harness writes, diffs, actions, copies."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.errors import ConflictError, GitError
from ratchet.store.archive import unpack
from ratchet.watcher._git import run_git
from ratchet.watcher.gitops import SkillsRepo, author_arg, author_kind

TRIAGE = """---
name: github-triage
description: Triage GitHub issues, label them by area and close obvious duplicates.
metadata:
  hermes:
    tags: [github]
---
# GitHub triage

1. List open issues.
"""

EXPENSE = """---
name: expense-report
description: Build an expense report from receipts and add the VAT line.
---
Sum the receipts.
"""


def write_seed(root: Path) -> Path:
    for name, text in (("github-triage", TRIAGE), ("expense-report", EXPENSE)):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(text, encoding="utf-8")
    (root / "github-triage" / "scripts").mkdir()
    (root / "github-triage" / "scripts" / "dedupe.py").write_text("print('x')\n")
    return root


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(write_seed(tmp_path / "seed"))
    return skills


def log_subjects(repo: SkillsRepo, ref: str) -> list[str]:
    return run_git(repo.main_dir, "log", "--format=%s", ref).out.split("\n")[:-1]


def test_init_creates_main_candidate_and_worktree(repo: SkillsRepo) -> None:
    assert repo.candidate_dir.name == "skills-candidate"
    assert (repo.candidate_dir / "github-triage" / "SKILL.md").read_text() == TRIAGE
    assert repo.main_head() == repo.candidate_head()
    assert repo.list_skills() == ["expense-report", "github-triage"]
    assert repo.commit_author_kind("main") == "seeded"


def test_init_is_idempotent_and_repairs_a_missing_worktree(repo: SkillsRepo) -> None:
    head = repo.main_head()
    run_git(repo.main_dir, "worktree", "remove", "--force", str(repo.candidate_dir))
    SkillsRepo(repo.main_dir).init()
    assert repo.main_head() == head
    assert (repo.candidate_dir / "expense-report" / "SKILL.md").exists()


def test_init_adopts_existing_files_as_seed(tmp_path: Path) -> None:
    write_seed(tmp_path / "hermes-skills")
    adopted = SkillsRepo(tmp_path / "hermes-skills")
    adopted.init()
    assert adopted.list_skills() == ["expense-report", "github-triage"]


def test_commit_candidate_returns_none_when_nothing_changed(repo: SkillsRepo) -> None:
    assert repo.commit_candidate("noop", author="user") is None


def test_commit_candidate_records_author_classification(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "Round up.\n")
    commit = repo.commit_candidate("edit", author="agent")
    assert commit == repo.candidate_head()
    assert repo.commit_author_kind(commit) == "agent"
    committer = run_git(repo.main_dir, "log", "-1", "--format=%cn <%ce>", commit).out.strip()
    assert committer == "Ratchet <ratchet@ratchet.local>"
    assert repo.main_head() != commit


def test_capture_main_changes_moves_harness_writes_and_restores_main(repo: SkillsRepo) -> None:
    main_tree = repo.head_tree_hash("main")
    (repo.main_dir / "github-triage" / "SKILL.md").write_text(TRIAGE + "2. Close stale.\n")
    (repo.main_dir / "release-notes").mkdir()
    (repo.main_dir / "release-notes" / "SKILL.md").write_text("---\nname: release-notes\n---\n")
    (repo.main_dir / "github-triage" / "scripts" / "dedupe.py").unlink()

    moved = repo.capture_main_changes()

    assert moved == [
        "github-triage/SKILL.md",
        "github-triage/scripts/dedupe.py",
        "release-notes/SKILL.md",
    ]
    assert repo.dirty_paths("main") == []
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_text() == TRIAGE
    assert (repo.main_dir / "github-triage" / "scripts" / "dedupe.py").exists()
    assert not (repo.main_dir / "release-notes").exists()
    assert repo.head_tree_hash("main") == main_tree
    assert repo.commit_author_kind("candidate") == "agent"
    assert not (repo.candidate_dir / "github-triage" / "scripts" / "dedupe.py").exists()
    assert repo.changed_files("main", "candidate") == moved


def test_capture_main_changes_without_commit_leaves_candidate_dirty(repo: SkillsRepo) -> None:
    (repo.main_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "x\n")
    assert repo.capture_main_changes(commit=False) == ["expense-report/SKILL.md"]
    assert repo.candidate_head() == repo.main_head()
    assert repo.dirty_paths("candidate") == ["expense-report/SKILL.md"]


def test_capture_ignores_swap_files(repo: SkillsRepo) -> None:
    (repo.main_dir / "github-triage" / ".SKILL.md.swp").write_text("junk")
    assert repo.capture_main_changes() == []


def test_ratchetignore_globs_are_honored(repo: SkillsRepo) -> None:
    (repo.candidate_dir / ".ratchetignore").write_text("# local notes\n*.notes\n")
    repo.refresh_excludes()
    (repo.candidate_dir / "github-triage" / "todo.notes").write_text("x")
    assert repo.dirty_paths("candidate") == [".ratchetignore"]


def test_diff_changed_files_and_affected_skills(repo: SkillsRepo) -> None:
    base = repo.main_head()
    (repo.candidate_dir / "github-triage" / "SKILL.md").write_text(TRIAGE + "More.\n")
    commit = repo.commit_candidate("e", author="user")
    assert commit is not None
    assert repo.changed_files(base, commit) == ["github-triage/SKILL.md"]
    assert "+More." in repo.diff(base, commit)
    assert repo.affected_skills(base, commit) == ["github-triage"]
    assert repo.numstat(base, commit) == {"github-triage/SKILL.md": (1, 0)}


def test_affected_skills_includes_name_references_and_nested_skills(
    repo: SkillsRepo,
) -> None:
    base = repo.main_head()
    shared = repo.candidate_dir / "shared"
    shared.mkdir()
    (shared / "expense-report.md").write_text("shared VAT rules\n")
    nested = repo.candidate_dir / "ops" / "deploy-check"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: deploy-check\ndescription: d\n---\n")
    (repo.candidate_dir / "github-triage" / "scripts" / "dedupe.py").unlink()
    commit = repo.commit_candidate("e", author="user")
    assert commit is not None
    assert repo.affected_skills(base, commit) == [
        "deploy-check",
        "expense-report",
        "github-triage",
    ]


def test_root_files_and_non_skill_dirs_affect_no_skill(repo: SkillsRepo) -> None:
    base = repo.main_head()
    (repo.candidate_dir / "README.md").write_text("hi\n")
    (repo.candidate_dir / "docs").mkdir()
    (repo.candidate_dir / "docs" / "notes.md").write_text("n\n")
    commit = repo.commit_candidate("e", author="user")
    assert commit is not None
    assert repo.affected_skills(base, commit) == []


def test_deleting_a_skill_affects_it(repo: SkillsRepo) -> None:
    base = repo.main_head()
    commit = repo.apply_files({"expense-report/SKILL.md": None}, message="rm", author="user")
    assert repo.affected_skills(base, commit) == ["expense-report"]


def test_skill_description_reads_any_ref(repo: SkillsRepo) -> None:
    old = repo.main_head()
    new_text = TRIAGE.replace("close obvious duplicates", "close stale issues")
    (repo.candidate_dir / "github-triage" / "SKILL.md").write_text(new_text)
    commit = repo.commit_candidate("e", author="user")
    assert commit is not None
    assert "duplicates" in repo.skill_description("github-triage", ref=old)
    assert "stale" in repo.skill_description("github-triage", ref=commit)
    assert repo.skill_description("missing-skill") == ""


def test_merge_candidate_fast_forwards_main_with_verdict_marker(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "VAT.\n")
    edit_commit = repo.commit_candidate("e", author="agent")
    assert edit_commit is not None
    merged = repo.merge_candidate("verdict_abc123")
    assert repo.main_head() == merged == repo.candidate_head()
    assert log_subjects(repo, "main")[0] == "ratchet: merge verdict verdict_abc123"
    assert repo.is_ancestor(edit_commit, merged)
    assert (repo.main_dir / "expense-report" / "SKILL.md").read_text() == EXPENSE + "VAT.\n"
    assert repo.dirty_paths("candidate") == []


def test_merge_of_an_earlier_candidate_commit_leaves_later_edits_pending(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "1\n")
    first = repo.commit_candidate("first", author="user")
    (repo.candidate_dir / "github-triage" / "SKILL.md").write_text(TRIAGE + "2\n")
    second = repo.commit_candidate("second", author="user")
    assert first is not None and second is not None
    assert repo.merge_candidate("verdict_1", commit=first) == first
    assert repo.main_head() == first
    assert repo.candidate_head() == second


def test_merge_is_a_noop_when_candidate_equals_main(repo: SkillsRepo) -> None:
    head = repo.main_head()
    assert repo.merge_candidate("verdict_x") == head


def test_merge_refuses_conflicting_uncommitted_main_writes(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "c\n")
    repo.commit_candidate("e", author="user")
    (repo.main_dir / "expense-report" / "SKILL.md").write_text("harness wip\n")
    candidate = repo.candidate_head()
    with pytest.raises(ConflictError) as info:
        repo.merge_candidate("verdict_y")
    assert info.value.code == "merge_conflict"
    assert repo.candidate_head() == candidate  # no marker commit left behind


def test_reset_candidate_discards_unmerged_edits(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text("changed\n")
    repo.commit_candidate("e", author="seeded")
    (repo.candidate_dir / "stray.txt").write_text("x")
    repo.reset_candidate()
    assert repo.candidate_head() == repo.main_head()
    assert repo.dirty_paths("candidate") == []
    assert (repo.candidate_dir / "expense-report" / "SKILL.md").read_text() == EXPENSE


def test_apply_files_commits_only_the_given_paths(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "github-triage" / "notes.md").write_text("user wip\n")
    commit = repo.apply_files(
        {
            "expense-report/SKILL.md": EXPENSE + "seeded\n",
            "github-triage/scripts/dedupe.py": None,
            "never-existed.txt": None,
        },
        message="ratchet: seeded scenario 1",
    )
    assert commit == repo.candidate_head()
    assert repo.commit_author_kind(commit) == "seeded"
    assert repo.changed_files("main", commit) == [
        "expense-report/SKILL.md",
        "github-triage/scripts/dedupe.py",
    ]
    assert repo.dirty_paths("candidate") == ["github-triage/notes.md"]


def test_apply_files_refuses_paths_outside_the_repo(repo: SkillsRepo) -> None:
    with pytest.raises(GitError):
        repo.apply_files({"../escape.txt": "x"}, message="bad")
    with pytest.raises(GitError):
        repo.apply_files({".git/config": "x"}, message="bad")


def test_apply_files_with_no_change_returns_head(repo: SkillsRepo) -> None:
    head = repo.candidate_head()
    assert repo.apply_files({"expense-report/SKILL.md": EXPENSE}, message="same") == head


def test_clone_to_is_isolated(repo: SkillsRepo, tmp_path: Path) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "pending\n")
    repo.commit_candidate("pending", author="user")
    clone = repo.clone_to(tmp_path / "sessions" / "s1" / "skills")
    assert clone.candidate_dir == tmp_path / "sessions" / "s1" / "skills-candidate"
    assert clone.main_head() == repo.main_head()
    assert clone.candidate_head() == repo.candidate_head()
    assert run_git(clone.main_dir, "remote").out.strip() == ""

    clone.apply_files({"github-triage/SKILL.md": "---\nname: github-triage\n---\n"}, message="s")
    clone.merge_candidate("verdict_clone")
    assert clone.main_head() != repo.main_head()
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_text() == TRIAGE
    assert repo.candidate_head() != clone.candidate_head()


def test_clone_to_refuses_a_non_empty_destination(repo: SkillsRepo, tmp_path: Path) -> None:
    dest = tmp_path / "busy"
    dest.mkdir()
    (dest / "file").write_text("x")
    with pytest.raises(GitError):
        repo.clone_to(dest)


def test_checkout_tar_packs_the_tree_at_a_ref(repo: SkillsRepo, tmp_path: Path) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text("candidate version\n")
    commit = repo.commit_candidate("e", author="user")
    assert commit is not None
    tar = repo.checkout_tar("candidate", tmp_path / "out" / "skills.tar.zst")
    written = unpack(tar, tmp_path / "unpacked")
    assert sorted(written) == [
        "expense-report/SKILL.md",
        "github-triage/SKILL.md",
        "github-triage/scripts/dedupe.py",
    ]
    assert (tmp_path / "unpacked" / "expense-report" / "SKILL.md").read_text() == (
        "candidate version\n"
    )
    again = repo.checkout_tar(commit, tmp_path / "out" / "again.tar.zst")
    assert again.read_bytes() == tar.read_bytes()


def test_head_tree_hash_is_stable_for_equal_trees(repo: SkillsRepo) -> None:
    assert repo.head_tree_hash("main") == repo.head_tree_hash("candidate")


def test_author_helpers() -> None:
    assert author_arg("seeded") == "Ratchet Seeded <seeded@ratchet.local>"
    assert author_arg("Jun Ito <jun@example.com>") == "Jun Ito <jun@example.com>"
    with pytest.raises(GitError):
        author_arg("nobody")
    assert author_kind("agent@ratchet.local") == "agent"
    assert author_kind("jun@example.com") == "user"


def test_git_errors_carry_codes(repo: SkillsRepo) -> None:
    with pytest.raises(GitError) as info:
        repo.resolve("no-such-ref")
    assert info.value.code == "git_error"
