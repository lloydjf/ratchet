"""Three rapid writes produce one candidate commit with the right affected skills.

Uses real watchdog observers with a short debounce; the 5 s poll fallback is disabled
(poll interval 60 s) so the edit can only come from file events.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from ratchet.models import SkillEdit
from ratchet.watcher._git import run_git
from ratchet.watcher.gitops import SkillsRepo
from ratchet.watcher.service import Watcher

TRIAGE = "---\nname: github-triage\ndescription: Triage GitHub issues.\n---\nList issues.\n"
EXPENSE = "---\nname: expense-report\ndescription: Expense reports.\n---\nSum.\n"


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    seed = tmp_path / "seed"
    for name, text in (("github-triage", TRIAGE), ("expense-report", EXPENSE)):
        (seed / name).mkdir(parents=True)
        (seed / name / "SKILL.md").write_text(text)
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(seed)
    return skills


class Collector:
    def __init__(self) -> None:
        self.edits: list[SkillEdit] = []
        self.event = threading.Event()

    def __call__(self, edit: SkillEdit, diff_text: str) -> None:
        self.edits.append(edit)
        self.event.set()


def commits_ahead(repo: SkillsRepo) -> int:
    return len(run_git(repo.main_dir, "rev-list", "main..candidate").out.split())


def test_three_rapid_harness_writes_make_one_candidate_commit(repo: SkillsRepo) -> None:
    sink = Collector()
    watcher = Watcher(repo, sink, debounce_ms=400, poll_interval_s=60)
    watcher.start()
    try:
        time.sleep(0.3)  # let the observers attach
        skill = repo.main_dir / "github-triage"
        (skill / "SKILL.md").write_text(TRIAGE + "1\n")
        time.sleep(0.05)
        (skill / "SKILL.md").write_text(TRIAGE + "1\n2\n")
        time.sleep(0.05)
        (skill / "references").mkdir()
        (skill / "references" / "labels.md").write_text("bug, docs\n")
        assert sink.event.wait(10), "watcher never emitted an edit"
        time.sleep(1.2)  # a second flush (from our own capture's events) must not emit
    finally:
        watcher.stop()

    assert len(sink.edits) == 1
    edit = sink.edits[0]
    assert commits_ahead(repo) == 1
    assert edit.commit == repo.candidate_head()
    assert edit.author == "agent"
    assert edit.affected_skills == ["github-triage"]
    assert edit.files == ["github-triage/SKILL.md", "github-triage/references/labels.md"]
    assert (repo.candidate_dir / "github-triage" / "SKILL.md").read_text() == TRIAGE + "1\n2\n"
    assert (repo.main_dir / "github-triage" / "SKILL.md").read_text() == TRIAGE


def test_user_edit_in_candidate_worktree_is_detected(repo: SkillsRepo) -> None:
    sink = Collector()
    watcher = Watcher(repo, sink, debounce_ms=300, poll_interval_s=60)
    watcher.start()
    try:
        time.sleep(0.3)
        (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "VAT\n")
        (repo.candidate_dir / "expense-report" / ".SKILL.md.swp").write_text("swap")
        assert sink.event.wait(10)
    finally:
        watcher.stop()
    assert [e.author for e in sink.edits] == ["user"]
    assert sink.edits[0].files == ["expense-report/SKILL.md"]


def test_poll_fallback_catches_changes_without_events(repo: SkillsRepo) -> None:
    (repo.candidate_dir / "expense-report" / "SKILL.md").write_text(EXPENSE + "pre\n")
    sink = Collector()
    watcher = Watcher(repo, sink, debounce_ms=100, poll_interval_s=0.3)
    watcher.start()
    try:
        assert sink.event.wait(10)
    finally:
        watcher.stop()
    assert len(sink.edits) == 1
