"""Acceptance for the HermesAdapter against the real Hermes checkout.

Runs ``hermes chat --oneshot`` from ``var/hermes/src`` (installed per
docs/research/hermes-live-s3.md) against Ratchet's proxy and mock tool server, both in
process on the control plane (``control_plane``: record mode, MCP on), with the offline
simulator upstream: no model is called. Acceptance:

- ``run_task`` on a seeded task yields ≥ 1 LLMCall (proxy) and ≥ 1 ToolCall (mock MCP);
- ``loaded_skills()`` returns the seeded skill;
- after editing SKILL.md and ``reload_skills()``, the next run's prompt reflects the edit;

plus cache stability: the first request of two runs with the same skills and task hashes the same.

Opt in with ``uv run pytest -m hermes``; skipped when the checkout is missing. Hermes homes
go under ``var/hermes/e2e-homes``; nothing touches ``~/.hermes``.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ratchet.capture.runs import RunMeta, write_run_meta
from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.mocktools.world import World
from ratchet.models import LLMCall, ToolCall
from ratchet.orchestrator.agent import ControlPlane, control_plane
from ratchet.replay.harness.hermes import HermesAdapter
from ratchet.store import Store

REPO = Path(__file__).resolve().parents[2]
HERMES_BIN = REPO / "var" / "hermes" / "src" / ".venv" / "bin" / "hermes"
HOMES = REPO / "var" / "hermes" / "e2e-homes"
SCENARIOS = REPO / "scenarios"
FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
TASK = "Triage the open issues in acme/widgets"
OLD = "Thanks for the question. Could you share"
NEW = "Thanks for asking! Please share"

pytestmark = [
    pytest.mark.hermes,
    pytest.mark.skipif(
        not HERMES_BIN.is_file(),
        reason="Hermes is not installed at var/hermes/src (see docs/research/hermes-live-s3.md)",
    ),
]


@dataclass(frozen=True)
class Stack:
    settings: Settings
    store: Store
    plane: ControlPlane
    skills: Path
    work: Path


@pytest.fixture
def stack(tmp_path: Path) -> Iterator[Stack]:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        ratchet_data_dir=tmp_path / "data",
        ratchet_upstream="sim",
        ratchet_tavily_mode="fixtures",
        ratchet_tavily_fixtures_dir=REPO / "tests" / "fixtures" / "tavily",
        ratchet_public_mode=False,
        ratchet_log_level="ERROR",
        nebius_token_factory_key=None,
        tavily_api_key=None,
    )
    configure(settings)
    skills = tmp_path / "skills"
    shutil.copytree(SCENARIOS / "skills" / "github-triage", skills / "github-triage")
    work = tmp_path / "work"
    work.mkdir()
    store = Store.open(settings.ratchet_data_dir)
    world = World.load(SCENARIOS / "world")
    try:
        with control_plane(settings, store, world) as plane:
            yield Stack(settings, store, plane, skills, work)
    finally:
        reset_settings()


def _run(stack: Stack, adapter: HermesAdapter, run_id: str) -> tuple[list[LLMCall], list[ToolCall]]:
    meta = RunMeta(run_id=run_id, agent_tier="super", seed=7, frozen_time=FROZEN, harness="hermes")
    write_run_meta(stack.store, meta)
    adapter.configure(
        proxy_url=stack.plane.proxy_url,
        mock_tools_url=stack.plane.mock_url,
        skills_dir=stack.skills,
        workspace=stack.work,
        frozen_time=FROZEN,
        seed=7,
        agent_tier="super",
        run_id=run_id,
    )
    result = adapter.run_task(TASK, timeout_s=240)
    assert result.exit_ok, Path(result.stderr_ref).read_text(encoding="utf-8")[-2000:]
    assert result.skills_invoked == ["github-triage"]
    assert result.final_output.startswith("Triaged acme/widgets:")
    assert result.harness_version.startswith("hermes/") and "unknown" not in result.harness_version
    steps = stack.store.collect_steps(run_id)
    return (
        [s for s in steps if isinstance(s, LLMCall)],
        [s for s in steps if isinstance(s, ToolCall)],
    )


def _requests_text(stack: Stack, run_id: str, calls: list[LLMCall]) -> str:
    return "\n".join(str(stack.store.read_step_payload(run_id, c.messages_ref)) for c in calls)


def test_hermes_adapter_meets_the_adapter_acceptance_criteria(stack: Stack) -> None:
    adapter = HermesAdapter(hermes_bin=str(HERMES_BIN), home_root=HOMES)
    adapter.configure(
        proxy_url=stack.plane.proxy_url,
        mock_tools_url=stack.plane.mock_url,
        skills_dir=stack.skills,
        workspace=stack.work,
        frozen_time=FROZEN,
        seed=7,
        agent_tier="super",
        run_id="hermes_acc_1",
    )
    assert adapter.loaded_skills() == ["github-triage"]

    llm, tools = _run(stack, adapter, "hermes_acc_1")
    assert len(llm) >= 1 and all(c.purpose == "agent" and c.tier == "super" for c in llm)
    assert "github.list_issues" in llm[0].tool_calls_requested + llm[1].tool_calls_requested
    assert len(tools) >= 1 and tools[0].tool == "github.list_issues"
    assert {t.tool for t in tools} >= {"github.label", "github.comment"}
    first_text = _requests_text(stack, "hermes_acc_1", llm)
    assert OLD in first_text and NEW not in first_text
    first_request = stack.store.read_step_payload("hermes_acc_1", llm[0].messages_ref)
    assert "hermes_acc_1" not in str(first_request)  # no run-specific path in the prompt

    skill_md = stack.skills / "github-triage" / "SKILL.md"
    skill_md.write_text(skill_md.read_text(encoding="utf-8").replace(OLD, NEW), encoding="utf-8")
    adapter.reload_skills()
    llm2, tools2 = _run(stack, adapter, "hermes_acc_2")
    second_text = _requests_text(stack, "hermes_acc_2", llm2)
    assert NEW in second_text and OLD not in second_text
    comments = [t for t in tools2 if t.tool == "github.comment"]
    assert comments and all(str(t.args_norm.get("body", "")).startswith(NEW) for t in comments)
    # same skills index + task → the same first request across runs.
    assert llm2[0].request_hash == llm[0].request_hash
