"""HermesAdapter: config generation, isolated HERMES_HOME, stream-json parsing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

from ratchet.replay.harness import get_adapter
from ratchet.replay.harness.base import HarnessConfig, HostPaths, SandboxPaths
from ratchet.replay.harness.hermes import (
    DISABLED_TOOLSETS,
    ENABLED_TOOLSETS,
    HermesAdapter,
    build_config,
    parse_stream,
    write_home,
)

SKILLS = Path(__file__).resolve().parents[1] / "fixtures" / "skills"
FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)

STREAM = "\n".join(
    [
        '{"type":"system","subtype":"init","model":"super","session_id":"s-1","timestamp":1}',
        '{"type":"tool_use","name":"skills_list","input":{},"timestamp":2}',
        '{"type":"tool_use","name":"skill_view","input":{"name":"triage-issues"},"timestamp":3}',
        '{"type":"tool_result","name":"skill_view","output":"...","timestamp":4}',
        '{"type":"tool_use","name":"mcp__ratchet_mock__github_list_issues",'
        '"input":{"repo":"acme/widgets"},"timestamp":5}',
        '{"type":"tool_use","name":"skill_view","input":{"name":"triage-issues"},"timestamp":6}',
        '{"type":"text","text":"Labelled 2 issues.","timestamp":7}',
        "not json at all",
        '{"type":"result","session_id":"s-1","exit_code":0,"result":"Labelled 2 issues.",'
        '"input_tokens":10,"output_tokens":5,"timestamp":8}',
    ]
)


def _cfg(tmp_path: Path) -> HarnessConfig:
    return HarnessConfig(
        proxy_url="http://127.0.0.1:8081/v1",
        mock_tools_url="http://127.0.0.1:8082",
        skills_dir=SKILLS,
        workspace=tmp_path,
        frozen_time=FROZEN,
        seed=7,
        agent_tier="super",
        run_id="run_h1",
    )


def test_config_points_hermes_at_proxy_path_form_and_mock_mcp(tmp_path):
    """C2: a named provider owns base_url, key and attribution headers."""
    config = build_config(_cfg(tmp_path))
    assert config["model"] == {"default": "super", "provider": "ratchet", "context_length": 256_000}
    provider = config["providers"]["ratchet"]
    assert provider["base_url"] == "http://127.0.0.1:8081/v1/r/run_h1"
    assert provider["api_key"] == "ratchet-dummy-key"
    assert provider["extra_headers"]["X-Ratchet-Run-Id"] == "run_h1"
    assert provider["extra_headers"]["X-Ratchet-Purpose"] == "agent"
    assert provider["extra_headers"]["X-Ratchet-Tier"] == "super"
    assert "custom" not in config["providers"]
    mcp = config["mcp_servers"]["ratchet_mock"]
    assert mcp["url"] == "http://127.0.0.1:8082/mcp"
    assert mcp["headers"] == {"X-Ratchet-Run-Id": "run_h1"}
    disabled = config["agent"]["disabled_toolsets"]
    assert {"terminal", "web", "browser", "messaging", "code_execution"} <= set(disabled)
    assert "skills" not in DISABLED_TOOLSETS and "file" not in DISABLED_TOOLSETS


def test_config_pins_prompt_and_turns_off_side_calls(tmp_path):
    """C3, C6, C7: direct MCP tools, no title call, no git Workspace block, fast failure."""
    config = build_config(_cfg(tmp_path))
    assert config["_config_version"] == 50
    assert config["tools"] == {"tool_search": {"enabled": "off"}}
    assert config["auxiliary"] == {"title_generation": {"enabled": False}}
    agent = config["agent"]
    assert agent["coding_context"] == "off" and agent["api_max_retries"] == 1
    assert agent["max_turns"] == 25
    assert config["skills"] == {"creation_nudge_interval": 0}


def test_write_home_creates_isolated_home_with_skills_copy_and_no_bundled_skills(tmp_path):
    home = write_home(tmp_path / "home", _cfg(tmp_path), SKILLS)
    loaded = yaml.safe_load((home / "config.yaml").read_text())
    assert loaded["model"]["provider"] == "ratchet"
    assert (home / ".env").read_text() == ""
    assert (home / ".no-bundled-skills").is_file()
    empty = tmp_path / "no-bundled-skills"
    assert empty.is_dir() and not any(empty.iterdir())
    assert (home / "skills" / "triage-issues" / "SKILL.md").is_file()
    write_home(home, _cfg(tmp_path), SKILLS)  # idempotent re-run


def test_parse_stream_extracts_final_text_tools_and_viewed_skills():
    run = parse_stream(STREAM)
    assert run.final_text == "Labelled 2 issues."
    assert run.exit_code == 0 and run.error is None and run.session_id == "s-1"
    assert run.skills_viewed == ["triage-issues"]
    assert run.tool_uses[2] == "mcp__ratchet_mock__github_list_issues"
    assert run.bad_lines == 1


def test_parse_stream_falls_back_to_text_events_and_reports_errors():
    stream = '{"type":"text","text":"partial "}\n{"type":"text","text":"answer"}\n'
    stream += '{"type":"result","exit_code":1,"error":"budget"}'
    run = parse_stream(stream)
    assert run.final_text == "partial answer" and run.exit_code == 1 and run.error == "budget"


def test_adapter_plan_and_collect_for_sandboxed_runs(tmp_path):
    adapter = get_adapter("hermes")
    assert isinstance(adapter, HermesAdapter)
    adapter.configure(
        proxy_url="http://10.0.0.1:9/v1",
        mock_tools_url="http://10.0.0.1:10",
        skills_dir=SKILLS,
        workspace=tmp_path,
        frozen_time=FROZEN,
        seed=1,
        agent_tier="super",
        run_id="run_h2",
    )
    host = HostPaths(work=tmp_path, skills=SKILLS, io=tmp_path / "io")
    inside = SandboxPaths(work="/work", skills="/skills", io="/io")
    plan = adapter.plan("triage please", timeout_s=60, host=host, inside=inside, python="python")
    assert plan.argv[:6] == ["hermes", "chat", "--oneshot", "-Q", "-q", "triage please"]
    assert plan.argv[plan.argv.index("--format") + 1] == "stream-json"
    assert plan.env["HERMES_HOME"] == "/io/hermes_home" and plan.env["HOME"] == "/io"
    assert plan.env["HERMES_BUNDLED_SKILLS"] == "/io/no-bundled-skills"
    assert "OPENAI_API_KEY" not in plan.env
    assert "--usage-file" not in plan.argv
    assert plan.argv[plan.argv.index("--run-budget") + 1] == "54"
    assert (host.io / "no-bundled-skills").is_dir()
    assert (host.io / "hermes_home" / "config.yaml").is_file()
    result = adapter.collect(0, STREAM, "", host=host)
    assert result.exit_ok and result.skills_invoked == ["triage-issues"]
    assert result.harness_version == "hermes/unknown"
    assert not adapter.collect(1, STREAM, "", host=host).exit_ok
    assert adapter.loaded_skills() == [
        "expense-report",
        "release-notes",
        "schedule-meeting",
        "triage-issues",
    ]
    adapter.reload_skills()  # no-op by design


def test_run_task_without_hermes_installed_is_a_harness_error(tmp_path):
    import pytest

    from ratchet.errors import HarnessError

    adapter = HermesAdapter(hermes_bin=str(tmp_path / "no-hermes"), home_root=tmp_path / "h")
    adapter.configure(
        proxy_url="http://p/v1",
        mock_tools_url="http://m",
        skills_dir=SKILLS,
        workspace=tmp_path,
        frozen_time=None,
        seed=None,
        agent_tier="super",
        run_id="run_h3",
    )
    with pytest.raises(HarnessError):
        adapter.run_task("x", timeout_s=5)


def test_toolsets_are_an_allowlist_of_mock_mcp_file_and_skills(tmp_path):
    """Only the mock MCP toolset, file and skills are enabled; nothing else."""
    config = build_config(_cfg(tmp_path))
    assert set(ENABLED_TOOLSETS) == {"ratchet_mock", "file", "skills"}
    assert config["platform_toolsets"] == {"cli": list(ENABLED_TOOLSETS)}
    assert not set(ENABLED_TOOLSETS) & set(config["agent"]["disabled_toolsets"])
    assert {"terminal", "code_execution", "web", "browser", "todo", "clarify"} <= set(
        DISABLED_TOOLSETS
    )
    adapter = HermesAdapter()
    adapter.configure(
        proxy_url="http://p/v1",
        mock_tools_url="http://m",
        skills_dir=SKILLS,
        workspace=tmp_path,
        frozen_time=None,
        seed=None,
        agent_tier="super",
        run_id="run_h1",
    )
    argv = adapter.argv("task", timeout_s=300)
    assert argv[argv.index("--toolsets") + 1] == "ratchet_mock,file,skills"
    assert argv[argv.index("--run-budget") + 1] == "270"


def test_hermes_is_refused_in_the_process_sandbox(tmp_path):
    """Hermes has real built-in tools; the unenforced process sandbox is refused."""
    from ratchet.config.settings import Settings
    from ratchet.orchestrator.replaying import sandbox_refusal

    def make(**kw):
        return Settings(_env_file=None, ratchet_data_dir=tmp_path / "d", **kw)  # type: ignore[call-arg]

    assert sandbox_refusal(make(ratchet_sandbox="process"), "hermes") is not None
    assert sandbox_refusal(make(ratchet_sandbox="process", ratchet_harness="hermes")) is not None
    assert sandbox_refusal(make(ratchet_sandbox="process"), "minimal") is None
    assert sandbox_refusal(make(ratchet_sandbox="docker"), "hermes") is None
    assert sandbox_refusal(make(ratchet_sandbox="openshell", ratchet_harness="hermes")) is None


_FAKE_HERMES = """#!/bin/sh
if [ "$1" = "--version" ]; then
  echo probe >> "{count}"
  echo "Hermes Agent v9.8.7 (2026.9.24) · stable"
  exit 0
fi
printf '{{"type":"system","subtype":"init","session_id":"s"}}\\n'
printf '{{"type":"result","exit_code":0,"text":"%s|%s|%s"}}\\n' \\
  "$HOME" "$HERMES_HOME" "$HERMES_BUNDLED_SKILLS"
"""


def _fake_hermes(tmp_path: Path) -> tuple[Path, Path]:
    count = tmp_path / "probes.txt"
    binary = tmp_path / "bin" / "hermes"
    binary.parent.mkdir()
    binary.write_text(_FAKE_HERMES.format(count=count), encoding="utf-8")
    binary.chmod(0o755)
    return binary, count


def _configured(adapter: HermesAdapter, tmp_path: Path, run_id: str) -> HermesAdapter:
    adapter.configure(
        proxy_url="http://p/v1",
        mock_tools_url="http://m",
        skills_dir=SKILLS,
        workspace=tmp_path,
        frozen_time=None,
        seed=None,
        agent_tier="super",
        run_id=run_id,
    )
    return adapter


def test_run_task_uses_a_run_independent_home_and_keeps_outputs_per_run(tmp_path):
    """C10: HOME / HERMES_HOME reach the system prompt, so they must not contain the run id."""
    binary, _ = _fake_hermes(tmp_path)
    root = tmp_path / "homes"
    adapter = HermesAdapter(hermes_bin=str(binary), home_root=root, version="0.22.2")
    first = _configured(adapter, tmp_path, "run_a").run_task("t", timeout_s=30)
    second = _configured(adapter, tmp_path, "run_b").run_task("t", timeout_s=30)
    assert first.exit_ok and first.final_output == second.final_output
    home, hermes_home, bundled = first.final_output.split("|")
    assert home == str(root / "current") and hermes_home == f"{home}/hermes_home"
    assert bundled == f"{home}/no-bundled-skills"
    assert "run_a" not in first.final_output
    assert first.stdout_ref == str(root / "run_a" / "stdout.jsonl")
    assert (root / "run_b" / "stderr.txt").is_file()
    assert first.harness_version == "hermes/0.22.2"


def test_harness_version_probes_hermes_once_per_process(tmp_path):
    """C8: no event carries the version; `hermes --version` runs once, never per run."""
    from ratchet.replay.harness.hermes_stream import hermes_version, parse_version

    binary, count = _fake_hermes(tmp_path)
    adapter = _configured(HermesAdapter(hermes_bin=str(binary)), tmp_path, "run_v")
    host = HostPaths(work=tmp_path, skills=SKILLS, io=tmp_path / "io")
    for _ in range(3):
        assert adapter.collect(0, STREAM, "", host=host).harness_version == "hermes/9.8.7"
    assert HermesAdapter(hermes_bin=str(binary)).version() == "9.8.7"
    assert count.read_text().splitlines() == ["probe"]
    assert hermes_version(str(binary), "v0.22.2") == "0.22.2"  # baked wins, no probe
    assert parse_version("Hermes Agent v0.22.2 (2026.9.24) · stable · local d923") == "0.22.2"
    assert parse_version("garbage") is None
