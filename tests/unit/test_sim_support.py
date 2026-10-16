"""Shared helpers for the simulator suites (imported as ``test_sim_support``), plus smoke tests
that the demo content loads.

The helpers drive the offline simulator the way MinimalRunner does (``ratchet.sim.dryrun``):
the pinned system prompt from ``build_system_prompt``, the simulator over ASGI, tool results
from the demo world through ``ratchet.mocktools`` adapters. ``to_trace`` turns a dry run into
a ``Trace`` so the real judge code (Level 0, cascade, drift, narrative) can be run against the
simulator's judge heuristics.
"""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import httpx
import yaml

from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.judge.structured import reset_structured_modes
from ratchet.llm.client import ModelClient
from ratchet.mocktools.world import World
from ratchet.models import HARD_SIDE_EFFECTS, EnvSnapshot, LLMCall, ToolCall, Trace
from ratchet.replay.harness.skills import Skill, load_skills
from ratchet.sim.agent.base import DEFAULT_NOW
from ratchet.sim.app import create_app
from ratchet.sim.dryrun import Blocker, DryRun, ModelStep, load_golden_tasks, run_task
from ratchet.skills import parse_skill_md, validate_skill
from ratchet.tools.catalog import CATALOG
from ratchet.util import canonical_json, hash_json, sha256_hex

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "scenarios"
SKILLS = SCENARIOS / "skills"
WORLD = SCENARIOS / "world"
TAVILY_SCENARIO3 = ROOT / "tests" / "fixtures" / "tavily" / "scenario3"
EDITS = {
    1: SCENARIOS / "1" / "edit" / "github-triage" / "SKILL.md",
    2: SCENARIOS / "2" / "edit" / "expense-report" / "SKILL.md",
    3: SCENARIOS / "3" / "edit" / "fx-expense-converter" / "SKILL.md",
}


def golden_tasks() -> list[dict[str, object]]:
    return load_golden_tasks(SCENARIOS)


def tasks_for(skill: str | None) -> list[dict[str, object]]:
    return [t for t in golden_tasks() if t["expected_skill"] == skill]


def skills_for(scenario: int | None = None) -> list[Skill]:
    """The demo skills, with scenario ``k``'s edited SKILL.md in place of the original."""
    skills = load_skills(SKILLS)
    if scenario is None:
        return skills
    path = EDITS[scenario]
    doc = parse_skill_md(path.read_text(encoding="utf-8"), path=path)
    edited = Skill(doc.name, doc.description.strip(), doc.body.strip(), path)
    return [edited if s.name == doc.name else s for s in skills]


def world_for(tmp: Path, *, overlay: bool = False) -> World:
    """The demo world; ``overlay`` applies scenario 3's world-fixture overrides."""
    dest = tmp / ("world_overlay" if overlay else "world")
    if not dest.exists():
        shutil.copytree(WORLD, dest)
        if overlay:
            for override in (SCENARIOS / "3" / "world").glob("*.json"):
                shutil.copy(override, dest / override.name)
    return World.load(dest)


@asynccontextmanager
async def sim_http() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://ratchet-sim") as client:
        yield client


async def run(
    task: dict[str, object] | str,
    *,
    world: World,
    scenario: int | None = None,
    blocked: Blocker | None = None,
    run_id: str | None = None,
    workspace: Path | None = None,
) -> DryRun:
    text = task if isinstance(task, str) else str(task["task_input"])
    rid = run_id or (str(task["task_id"]) if isinstance(task, dict) else "adhoc")
    async with sim_http() as client:
        return await run_task(
            client,
            text,
            skills_for(scenario),
            world=world,
            run_id=f"{rid}-{scenario or 0}",
            workspace=workspace,
            blocked=blocked,
        )


def cassette_blocker(golden: DryRun) -> Blocker:
    """Replay stand-in: a hard-class call the golden never made is a cassette miss → 409."""
    seen = {(s.tool, canonical_json(s.args)) for s in golden.tool_steps}

    def blocked(tool: str, args: dict[str, object]) -> bool:
        spec = CATALOG.get(tool)
        hard = spec is not None and spec.side_effect in HARD_SIDE_EFFECTS
        return hard and (tool, canonical_json(args)) not in seen

    return blocked


def to_trace(
    run: DryRun,
    run_id: str,
    *,
    kind: str = "golden",
    golden_of: str | None = None,
    label: str = "",
) -> Trace:
    steps: list[LLMCall | ToolCall] = []
    for step in run.steps:
        if isinstance(step, ModelStep):
            usage = step.usage
            steps.append(
                LLMCall(
                    step_id=step.step_id,
                    tier="super",
                    model="ratchet-sim/super",
                    request_hash=sha256_hex(step.step_id + step.content),
                    messages_ref=f"steps/{step.step_id}.req.json",
                    response_ref=f"steps/{step.step_id}.res.json",
                    prompt_tokens=int(str(usage["prompt_tokens"])),
                    completion_tokens=int(str(usage["completion_tokens"])),
                    usd=0.0,
                    latency_ms=1,
                    started_at=DEFAULT_NOW,
                    tool_calls_requested=step.tools_requested,
                    content_preview=step.content[:400],
                )
            )
            continue
        result = canonical_json(step.result)
        steps.append(
            ToolCall(
                step_id=step.step_id,
                tool=step.tool,
                args_norm=step.args,
                args_hash=hash_json({"tool": step.tool, "args": step.args}),
                result_hash=sha256_hex(result),
                side_effect=CATALOG[step.tool].side_effect,
                source="local" if CATALOG[step.tool].local else "cassette",
                cassette_key=None,
                result_preview=result[:400],
                blocked=step.blocked,
                started_at=DEFAULT_NOW,
            )
        )
    return Trace(
        run_id=run_id,
        kind=kind,  # type: ignore[arg-type]
        task_input=run.task,
        steps=steps,
        final_output=run.final_output,
        artifacts_ref=None,
        skills_invoked=run.skills_invoked,
        world_dependent=False,
        world_facts=[],
        env=EnvSnapshot(
            workspace_ref=f"traces/{run_id}/workspace.tar.zst",
            frozen_time=DEFAULT_NOW,
            harness_version="minimal/sim",
            skills_commit="a" * 40,
        ),
        agent_tier="super",
        seed=7,
        created_at=DEFAULT_NOW,
        golden_of=golden_of,
        candidate_commit=None if kind == "golden" else "b" * 40,
        task_label=label,
    )


def unexpected_calls(replay: Trace) -> list[ToolCall]:
    return [s for s in replay.steps if isinstance(s, ToolCall) and s.blocked]


@contextmanager
def sim_settings(tmp: Path) -> Iterator[Settings]:
    reset_structured_modes()
    settings = configure(
        Settings(
            ratchet_data_dir=tmp / "data",
            ratchet_upstream="sim",
            ratchet_proxy_url="http://ratchet-sim/v1",
        )
    )
    try:
        yield settings
    finally:
        reset_settings()
        reset_structured_modes()


def model_client(settings: Settings, run_id: str = "judge_sim") -> ModelClient:
    """The real judge ModelClient, pointed straight at the simulator over ASGI."""
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()))
    return ModelClient(run_id=run_id, settings=settings, http=http)


# --- smoke tests ------------------------------------------------------------------------------
def test_demo_skills_and_scenario_edits_are_valid_agentskills() -> None:
    paths = sorted(SKILLS.glob("*/SKILL.md")) + list(EDITS.values())
    assert len(paths) == 8
    for path in paths:
        doc = parse_skill_md(path.read_text(encoding="utf-8"), path=path)
        assert validate_skill(doc) == [], path


def test_golden_set_has_the_designed_groups() -> None:
    tasks = golden_tasks()
    assert len(tasks) == 25
    assert len({t["task_id"] for t in tasks}) == 25
    groups = {s: len(tasks_for(s)) for s in {t["expected_skill"] for t in tasks}}
    assert groups == {"github-triage": 11, "expense-report": 7, "fx-expense-converter": 4, None: 3}
    assert [t["task_id"] for t in tasks if t["canary"]] == [t["task_id"] for t in tasks_for(None)]
    assert all(t["task_label"] and t["workspace"] is None for t in tasks)


def test_demo_world_loads_with_frozen_time_and_synthetic_people() -> None:
    world = World.load(WORLD)
    assert world.frozen_time == DEFAULT_NOW
    state = world.state("smoke")
    assert len(state.github.repos) == 11
    assert all(name.startswith("acme/") for name in state.github.repos)
    assert state.fx.rates["EUR/USD"]["2026-10-14"] == 1.08
    addresses = [state.mailbox.address] + [m.sender for m in state.mailbox.messages]
    assert all(a.endswith("example.com") for a in addresses)


def test_scenario_manifests_have_the_pinned_fields() -> None:
    keys = {"scenario_id", "skill", "title", "learned", "catches", "expected_decision"}
    for k, decision in ((1, "BLOCK"), (2, "BLOCK"), (3, "PASS")):
        data = yaml.safe_load((SCENARIOS / str(k) / "scenario.yaml").read_text())
        # live_world (optional): ratchet/orchestrator/live_world.py, scenario 3 on live Tavily
        assert keys | {"edit_summary"} == set(data) - {"live_world"}
        assert data["scenario_id"] == k
        assert data["expected_decision"] == decision
        assert EDITS[k].parent.name == data["skill"]
