"""Watcher: flush semantics, author classification, ignore rules, summaries (poll_once)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.models import SkillEdit
from ratchet.watcher.edits import build_edit, summarize
from ratchet.watcher.gitops import AuthorKind, SkillsRepo
from ratchet.watcher.service import ChangeSet, Watcher, default_author

TRIAGE = "---\nname: github-triage\ndescription: Triage GitHub issues.\n---\nList issues.\n"
EXPENSE = "---\nname: expense-report\ndescription: Expense reports from receipts.\n---\nSum.\n"


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    seed = tmp_path / "seed"
    for name, text in (("github-triage", TRIAGE), ("expense-report", EXPENSE)):
        (seed / name).mkdir(parents=True)
        (seed / name / "SKILL.md").write_text(text)
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(seed)
    return skills


class Sink:
    def __init__(self) -> None:
        self.edits: list[tuple[SkillEdit, str]] = []

    def __call__(self, edit: SkillEdit, diff_text: str) -> None:
        self.edits.append((edit, diff_text))


def test_poll_once_without_changes_emits_nothing(repo: SkillsRepo) -> None:
    sink = Sink()
    assert Watcher(repo, sink).poll_once() is None
    assert sink.edits == []


def test_harness_write_in_main_becomes_an_agent_edit(repo: SkillsRepo) -> None:
    sink = Sink()
    parent = repo.main_head()
    (repo.main_dir / "github-triage" / "SKILL.md").write_text(TRIAGE + "Close stale.\n")
    edit = Watcher(repo, sink).poll_once()
    assert edit is not None
    assert sink.edits[0][0] == edit
    assert edit.author == "agent"
    assert edit.parent == parent == repo.main_head()
    assert edit.commit == repo.candidate_head()
    assert edit.files == ["github-triage/SKILL.md"]
    assert edit.affected_skills == ["github-triage"]
    assert edit.edit_id.startswith("edit_")
    assert edit.summary == "github-triage: SKILL.md changed (+1 −0)"
    assert "+Close stale." in sink.edits[0][1]
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_text() == TRIAGE


def test_candidate_worktree_write_is_a_user_edit(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "VAT.\n")
    edit = Watcher(repo, Sink()).poll_once()
    assert edit is not None and edit.author == "user"


def test_writes_in_both_trees_fold_into_one_commit(repo: SkillsRepo) -> None:
    base = repo.candidate_head()
    (repo.main_dir / "github-triage" / "SKILL.md").write_text(TRIAGE + "a\n")
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "b\n")
    seen: list[ChangeSet] = []

    def resolver(changes: ChangeSet) -> AuthorKind:
        seen.append(changes)
        return "agent"

    edit = Watcher(repo, Sink(), author_resolver=resolver).poll_once()
    assert edit is not None
    assert seen == [ChangeSet(("github-triage/SKILL.md",), ("expense-report/SKILL.md",))]
    assert edit.affected_skills == ["expense-report", "github-triage"]
    assert edit.summary == "expense-report, github-triage: 2 files changed (+2 −0)"
    assert repo.resolve(f"{edit.commit}~1") == base


def test_default_author_heuristic() -> None:
    assert default_author(ChangeSet(("a",), ())) == "agent"
    assert default_author(ChangeSet((), ("a",))) == "user"


def test_session_hash_is_stamped_on_edits(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text("x\n")
    edit = Watcher(repo, Sink(), session_hash="sess_1").poll_once()
    assert edit is not None and edit.session_hash == "sess_1"


def test_a_failing_consumer_does_not_break_the_watcher(repo: SkillsRepo) -> None:
    def boom(edit: SkillEdit, diff_text: str) -> None:
        raise RuntimeError("consumer failed")

    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text("x\n")
    watcher = Watcher(repo, boom)
    assert watcher.poll_once() is not None
    assert watcher.poll_once() is None


def test_ignore_rules(repo: SkillsRepo) -> None:
    (repo.main_dir / ".ratchetignore").write_text("drafts/*\n")
    watcher = Watcher(repo, Sink())
    root = repo.main_dir
    assert watcher.is_ignored(root, root / ".git" / "index")
    assert watcher.is_ignored(root, root / "github-triage" / ".SKILL.md.swp")
    assert watcher.is_ignored(root, root / "github-triage" / "SKILL.md~")
    assert watcher.is_ignored(root, root / "drafts" / "idea.md")
    assert watcher.is_ignored(root, root)
    assert watcher.is_ignored(root, Path("/elsewhere/file"))
    assert not watcher.is_ignored(root, root / "github-triage" / "SKILL.md")


def test_ignored_files_never_become_edits(repo: SkillsRepo) -> None:
    (repo.main_dir / "github-triage" / ".SKILL.md.swp").write_text("swap")
    (repo.candidate_dir / "github-triage" / "notes~").write_text("backup")
    assert Watcher(repo, Sink()).poll_once() is None


def test_build_edit_for_a_seeded_commit(repo: SkillsRepo) -> None:
    commit = repo.apply_files({"github-triage/SKILL.md": TRIAGE + "seeded\n"}, message="seed")
    edit, diff_text = build_edit(repo, commit=commit, author="seeded", session_hash="s")
    assert edit.author == "seeded" and edit.commit == commit
    assert edit.parent == repo.main_head()
    assert edit.affected_skills == ["github-triage"]
    assert "+seeded" in diff_text


def test_summarize_variants() -> None:
    assert summarize({"ops/deploy/SKILL.md": (3, 1)}, ["deploy"]) == (
        "deploy: SKILL.md changed (+3 −1)"
    )
    assert summarize({"README.md": (1, 0)}, []) == "skills repo: README.md changed (+1 −0)"
