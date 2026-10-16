"""OpenClaw read-only compatibility.

An OpenClaw workspace keeps skills at ``<workspace>/skills/<name>/SKILL.md`` with OpenClaw
frontmatter extras (``homepage``, ``user-invocable``, single-line JSON ``metadata.openclaw``).
Pointed at that directory as the skills repo, the watcher detects an edit, resolves
``affected_skills`` to the directory name, and the MinimalRunner replays with the edited
skill injected.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ratchet.models import SkillEdit
from ratchet.replay.harness.minimal import MinimalRunner
from ratchet.skills import load_skills as load_skill_docs
from ratchet.watcher.gitops import SkillsRepo
from ratchet.watcher.service import Watcher

LAYOUT = Path(__file__).resolve().parents[1] / "fixtures" / "skills" / "openclaw-layout"
SKILLS = LAYOUT / "workspace" / "skills"
TASK = "Send payment reminders for the overdue invoices"
EDIT = "4. Copy the account manager on every reminder.\n"


class Collector:
    def __init__(self) -> None:
        self.edits: list[SkillEdit] = []
        self.event = threading.Event()

    def __call__(self, edit: SkillEdit, diff_text: str) -> None:
        self.edits.append(edit)
        self.event.set()


@pytest.fixture()
def repo(tmp_path: Path) -> SkillsRepo:
    seed = tmp_path / "seed"
    shutil.copytree(SKILLS, seed)
    skills = SkillsRepo(tmp_path / "skills")
    skills.init(seed)
    return skills


def test_openclaw_frontmatter_is_read_as_agentskills_with_extras_kept() -> None:
    docs = load_skill_docs(SKILLS, strict=True)  # strict: any violation would raise
    assert [d.name for d in docs] == ["invoice-reminder", "standup-digest"]
    invoice = docs[0]
    assert invoice.metadata["openclaw"]["primaryEnv"] == "SMTP_HOST"  # type: ignore[index]
    assert invoice.frontmatter["user-invocable"] is True
    assert invoice.frontmatter["homepage"] == "https://example.com/skills/invoice-reminder"


def _runner(server: list[httpx.Request], workspace: Path, skills_dir: Path) -> MinimalRunner:
    def handler(request: httpx.Request) -> httpx.Response:
        server.append(request)
        message = {"role": "assistant", "content": "Drafted 2 reminders."}
        return httpx.Response(200, json={"choices": [{"index": 0, "message": message}]})

    runner = MinimalRunner(transport=httpx.MockTransport(handler), log_dir=workspace / "logs")
    runner.configure(
        proxy_url="http://proxy.test/v1",
        mock_tools_url="http://mock.test",
        skills_dir=skills_dir,
        workspace=workspace,
        frozen_time=datetime(2026, 10, 14, 16, 0, tzinfo=UTC),
        seed=42,
        agent_tier="super",
        run_id="run_openclaw",
    )
    return runner


def test_watcher_detects_an_openclaw_edit_and_minimal_runner_replays_with_it(
    repo: SkillsRepo, tmp_path: Path
) -> None:
    sink = Collector()
    watcher = Watcher(repo, sink, debounce_ms=300, poll_interval_s=60)
    watcher.start()
    try:
        time.sleep(0.3)  # let the observers attach
        skill_md = repo.main_dir / "invoice-reminder" / "SKILL.md"
        skill_md.write_text(skill_md.read_text(encoding="utf-8") + EDIT, encoding="utf-8")
        assert sink.event.wait(10), "watcher never emitted an edit"
    finally:
        watcher.stop()
    (edit,) = sink.edits
    assert edit.affected_skills == ["invoice-reminder"]
    assert edit.files == ["invoice-reminder/SKILL.md"]
    assert edit.commit == repo.candidate_head()

    requests: list[httpx.Request] = []
    workspace = tmp_path / "work"
    workspace.mkdir()
    result = _runner(requests, workspace, repo.candidate_dir).run_task(TASK, timeout_s=30)
    assert result.exit_ok and result.skills_invoked == ["invoice-reminder"]
    system = json.loads(requests[0].content)["messages"][0]["content"]
    assert "## Skill: invoice-reminder" in system
    assert EDIT.strip() in system  # the candidate (edited) skill is what replays
    assert "## Skill: standup-digest" not in system
    assert "metadata:" not in system  # frontmatter, OpenClaw extras included, is stripped
