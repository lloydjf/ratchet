"""MinimalRunner against a scripted fake OpenAI server (httpx.MockTransport)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from ratchet.models import HarnessResult
from ratchet.replay.harness.base import EXIT_BUDGET, EXIT_OK, HostPaths, SandboxPaths
from ratchet.replay.harness.minimal import BLOCKED_RESULT, MinimalRunner, build_system_prompt, main
from ratchet.replay.harness.skills import load_skills

SKILLS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"
FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)

Reply = Callable[[dict[str, object], int], dict[str, object]]


def tool_call(name: str, args: dict[str, object], call_id: str = "call_1") -> dict[str, object]:
    fn = {"name": name, "arguments": json.dumps(args)}
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": fn}],
    }


def final(text: str) -> dict[str, object]:
    return {"role": "assistant", "content": text}


class FakeServer:
    """Records every request; answers model calls from a script and tool calls from a map."""

    def __init__(
        self, script: list[dict[str, object]], tools: dict[str, httpx.Response] | None = None
    ):
        self.script = script
        self.tools = tools or {}
        self.chat_requests: list[httpx.Request] = []
        self.tool_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/chat/completions"):
            self.chat_requests.append(request)
            index = min(len(self.chat_requests) - 1, len(self.script) - 1)
            body = {"choices": [{"index": 0, "message": self.script[index]}]}
            return httpx.Response(200, json=body)
        self.tool_requests.append(request)
        tool = path.removeprefix("/tools/").removesuffix("/notify")
        return self.tools.get(tool, httpx.Response(200, json={"ok": True, "result": {"n": 1}}))

    def chat_body(self, index: int) -> dict[str, object]:
        return json.loads(self.chat_requests[index].content)


def runner_for(
    server: FakeServer,
    workspace: Path,
    *,
    frozen: datetime | None = FROZEN,
    max_iterations: int = 25,
) -> MinimalRunner:
    runner = MinimalRunner(
        transport=httpx.MockTransport(server),
        max_iterations=max_iterations,
        log_dir=workspace.parent / "logs",
    )
    runner.configure(
        proxy_url="http://proxy.test/v1",
        mock_tools_url="http://mock.test",
        skills_dir=SKILLS,
        workspace=workspace,
        frozen_time=frozen,
        seed=42,
        agent_tier="super",
        run_id="run_abc",
    )
    return runner


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "notes.txt").write_text("line one\nline two\n")
    return ws


def test_runs_tool_loop_against_the_proxy_path_form(workspace):
    server = FakeServer(
        [tool_call("github__list_issues", {"repo": "acme/widgets"}), final("2 open")]
    )
    result = runner_for(server, workspace).run_task(
        "Triage the open issues in acme/widgets", timeout_s=30
    )
    assert result.exit_ok and result.final_output == "2 open"
    assert result.harness_version.startswith("minimal/")
    first = server.chat_requests[0]
    assert first.url.path == "/v1/r/run_abc/chat/completions"
    assert first.headers["X-Ratchet-Run-Id"] == "run_abc"
    assert first.headers["X-Ratchet-Purpose"] == "agent"
    assert first.headers["X-Ratchet-Tier"] == "super"
    assert first.headers["Authorization"] == "Bearer ratchet-dummy-key"
    body = server.chat_body(0)
    assert body["temperature"] == 0 and body["seed"] == 42 and body["max_tokens"] == 1200
    names = {t["function"]["name"] for t in body["tools"]}
    assert {
        "github__list_issues",
        "fs__read",
        "fs__write",
        "fs__delete",
        "fs__list",
        "shell__run",
        "web__search",
        "time__now",
    } <= names
    (tool_req,) = server.tool_requests
    assert tool_req.url.path == "/tools/github.list_issues"
    assert json.loads(tool_req.content) == {"args": {"repo": "acme/widgets"}}
    assert tool_req.headers["X-Ratchet-Run-Id"] == "run_abc"
    assert tool_req.headers["X-Ratchet-Frozen-Time"].startswith("2026-10-14T16:00:00")
    tool_msg = server.chat_body(1)["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call_1"
    assert json.loads(tool_msg["content"]) == {"n": 1}


def test_injects_matching_skills_into_the_pinned_prompt(workspace):
    server = FakeServer([final("ok")])
    result = runner_for(server, workspace).run_task(
        "Triage the open issues in acme/widgets", timeout_s=30
    )
    system = server.chat_body(0)["messages"][0]["content"]
    assert "Current date: 2026-10-14T16:00:00+00:00" in system
    assert "Available skills:\n- expense-report: " in system
    assert "## Skill: triage-issues\n# Triage issues" in system
    assert "## Skill: expense-report" not in system
    assert result.skills_invoked == ["triage-issues"]


def test_build_system_prompt_is_pure_and_pinned():
    skills = load_skills(SKILLS)
    triage = [s for s in skills if s.name == "triage-issues"]
    prompt = build_system_prompt(skills, triage, FROZEN)
    assert prompt == build_system_prompt(skills, triage, FROZEN)
    lines = prompt.splitlines()
    assert "Current date: 2026-10-14T16:00:00+00:00" in lines
    assert lines[lines.index("Available skills:") + 1].startswith("- expense-report: ")
    assert "## Skill: triage-issues" in lines
    assert "name: triage-issues" not in prompt  # frontmatter stripped


def test_blocked_tool_result_is_returned_to_the_model(workspace):
    blocked = httpx.Response(409, json={"error": {"code": "ratchet_blocked", "message": "no"}})
    server = FakeServer(
        [tool_call("gmail__send", {"to": ["a@example.com"]}), final("could not send")],
        tools={"gmail.send": blocked},
    )
    result = runner_for(server, workspace).run_task("email the team", timeout_s=30)
    assert result.exit_ok
    tool_msg = server.chat_body(1)["messages"][-1]
    assert json.loads(tool_msg["content"]) == BLOCKED_RESULT


def test_iteration_cap_stops_the_loop_without_failing(workspace):
    server = FakeServer([tool_call("time__now", {})])
    result = runner_for(server, workspace, max_iterations=3).run_task("loop forever", timeout_s=30)
    assert len(server.chat_requests) == 3
    assert result.exit_ok and result.final_output == ""


def test_local_reads_run_in_workspace_and_are_notified(workspace):
    server = FakeServer([tool_call("fs__read", {"path": "notes.txt"}), final("read it")])
    runner_for(server, workspace).run_task("read my notes", timeout_s=30)
    (notify,) = server.tool_requests
    assert notify.url.path == "/tools/fs.read/notify"
    payload = json.loads(notify.content)
    assert payload["args"] == {"path": "notes.txt"}
    assert payload["result"]["content"].startswith("line one")


def test_local_write_is_not_notified_but_lands_in_workspace(workspace):
    server = FakeServer(
        [tool_call("fs__write", {"path": "out/a.md", "content": "hi"}), final("ok")]
    )
    runner_for(server, workspace).run_task("write a file", timeout_s=30)
    assert (workspace / "out" / "a.md").read_text() == "hi"
    assert server.tool_requests == []


def test_path_escape_is_refused(workspace):
    server = FakeServer([tool_call("fs__read", {"path": "../../etc/passwd"}), final("no")])
    runner_for(server, workspace).run_task("read secrets", timeout_s=30)
    content = json.loads(server.chat_body(1)["messages"][-1]["content"])
    assert content == {"error": "path escapes the workspace"}


def test_shell_allowlist_refuses_writes_and_runs_reads(workspace):
    server = FakeServer(
        [
            tool_call("shell__run", {"cmd": "rm -rf ."}, "c1"),
            tool_call("shell__run", {"cmd": "wc -l notes.txt"}, "c2"),
            final("done"),
        ]
    )
    runner_for(server, workspace).run_task("count lines", timeout_s=30)
    refused = json.loads(server.chat_body(1)["messages"][-1]["content"])
    assert "not allowed" in refused["error"]
    counted = json.loads(server.chat_body(2)["messages"][-1]["content"])
    assert counted["exit_code"] == 0 and "2" in counted["stdout"]
    assert (workspace / "notes.txt").exists()


def test_time_now_answers_the_frozen_time(workspace):
    server = FakeServer([tool_call("time__now", {}), final("it is time")])
    runner_for(server, workspace).run_task("what time is it", timeout_s=30)
    content = json.loads(server.chat_body(1)["messages"][-1]["content"])
    assert content == {"now": "2026-10-14T16:00:00+00:00"}


def test_unknown_tool_and_bad_arguments_become_tool_errors(workspace):
    bad = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "nope__tool", "arguments": "{}"}},
            {"id": "c2", "type": "function", "function": {"name": "fs__read", "arguments": "[1]"}},
        ],
    }
    server = FakeServer([bad, final("ok")])
    runner_for(server, workspace).run_task("x", timeout_s=30)
    messages = server.chat_body(1)["messages"]
    assert json.loads(messages[-2]["content"]) == {"error": "unknown tool"}
    assert "JSON object" in json.loads(messages[-1]["content"])["error"]


def test_proxy_error_is_a_harness_failure(workspace):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {"code": "tier_mismatch"}})

    runner = MinimalRunner(transport=httpx.MockTransport(handler), log_dir=workspace.parent / "l")
    runner.configure(
        proxy_url="http://p/v1",
        mock_tools_url="http://m",
        skills_dir=SKILLS,
        workspace=workspace,
        frozen_time=None,
        seed=None,
        agent_tier="nano",
        run_id="run_x",
    )
    result = runner.run_task("anything", timeout_s=10)
    assert not result.exit_ok and runner.last_error_code == "tier_mismatch"


def test_budget_exceeded_is_reported_for_exit_code_6(workspace):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"code": "budget_exceeded"}})

    runner = MinimalRunner(transport=httpx.MockTransport(handler), log_dir=workspace.parent / "l")
    runner.configure(
        proxy_url="http://p/v1",
        mock_tools_url="http://m",
        skills_dir=SKILLS,
        workspace=workspace,
        frozen_time=None,
        seed=1,
        agent_tier="super",
        run_id="run_x",
    )
    assert not runner.run_task("anything", timeout_s=10).exit_ok
    assert runner.last_error_code == "budget_exceeded"


def test_loaded_skills_reload_and_snapshot(workspace, tmp_path):
    skills = tmp_path / "skills"
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: Alpha things.\n---\nA"
    )
    runner = MinimalRunner(log_dir=tmp_path / "logs")
    runner.configure(
        proxy_url="http://p/v1",
        mock_tools_url="http://m",
        skills_dir=skills,
        workspace=workspace,
        frozen_time=None,
        seed=1,
        agent_tier="super",
        run_id="run_s",
    )
    assert runner.loaded_skills() == ["alpha"]
    (skills / "beta").mkdir()
    (skills / "beta" / "SKILL.md").write_text("---\nname: beta\ndescription: Beta things.\n---\nB")
    runner.reload_skills()
    assert runner.loaded_skills() == ["alpha", "beta"]
    assert runner.pre_task_snapshot().is_file()


def test_plan_and_collect_round_trip(workspace, tmp_path):
    runner = runner_for(FakeServer([final("x")]), workspace)
    host = HostPaths(work=workspace, skills=SKILLS, io=tmp_path / "io")
    host.io.mkdir()
    inside = SandboxPaths(work="/work", skills="/skills", io="/io")
    plan = runner.plan("do it", timeout_s=60, host=host, inside=inside, python="python")
    assert plan.argv[:4] == ["python", "-I", "-m", "ratchet.replay.harness.minimal"]
    assert (host.io / "task.txt").read_text() == "do it"
    assert "do it" not in plan.argv  # task text travels in a file, not argv
    assert plan.argv[plan.argv.index("--frozen-time") + 1] == "2026-10-14T16:00:00+00:00"
    missing = runner.collect(0, "", "", host=host)
    assert not missing.exit_ok
    ok = HarnessResult(
        final_output="y",
        exit_ok=True,
        stdout_ref="",
        stderr_ref="",
        skills_invoked=[],
        harness_version="minimal/0.1.0",
    )
    (host.io / "result.json").write_text(ok.model_dump_json())
    assert runner.collect(0, "", "", host=host).exit_ok
    assert not runner.collect(5, "", "", host=host).exit_ok


def test_cli_writes_result_and_returns_exit_code(workspace, tmp_path, monkeypatch):
    server = FakeServer([final("cli done")])
    original = httpx.Client.__init__

    def patched(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["transport"] = httpx.MockTransport(server)
        original(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched)
    out = tmp_path / "res" / "result.json"
    out.parent.mkdir()
    code = main(
        [
            "--task",
            "hi",
            "--skills",
            str(SKILLS),
            "--workspace",
            str(workspace),
            "--proxy",
            "http://p/v1",
            "--mock",
            "http://m",
            "--run-id",
            "run_cli",
            "--tier",
            "super",
            "--seed",
            "3",
            "--out",
            str(out),
        ]
    )
    assert code == EXIT_OK
    assert HarnessResult.model_validate_json(out.read_text()).final_output == "cli done"
    assert EXIT_BUDGET == 6
