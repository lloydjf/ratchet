"""The three seeded scenarios end to end on the simulator (design doc §3, ADR 0018).

Goldens: the 25 demo tasks run with the original skills on the demo world, enriched with
simulated bless facts and criteria. Selection: direct goldens of the edited skill plus the 3
canaries. Replays: the edited skills (scenario 3 also on the moved world), with hard-class
calls the golden never made answered as blocked cassette misses. Then the real Level 0,
cascade, drift check (scenario 3 Tavily fixtures), decision table and narrative.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sim_support import (  # noqa: E402
    TAVILY_SCENARIO3,
    cassette_blocker,
    golden_tasks,
    model_client,
    run,
    sim_settings,
    to_trace,
    unexpected_calls,
    world_for,
)

from ratchet.config.settings import Settings  # noqa: E402
from ratchet.judge import bless, level0  # noqa: E402
from ratchet.judge.cascade import PairContext, judge_pair  # noqa: E402
from ratchet.judge.decide import decide  # noqa: E402
from ratchet.judge.drift import FixtureTavily, check_pair  # noqa: E402
from ratchet.judge.narrative import NarrativeInput, validate_narrative, write_narrative  # noqa: E402
from ratchet.llm.client import ModelClient  # noqa: E402
from ratchet.models import PairVerdict  # noqa: E402

SKILL = {1: "github-triage", 2: "expense-report", 3: "fx-expense-converter"}


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    with sim_settings(tmp_path) as s:
        yield s


def scenario_diff(k: int, skill: str) -> str:
    """The real unified diff of the scenario's edit (an empty diff now reads as "nothing to
    attribute", so the judges must see what changed)."""
    import difflib

    root = Path(__file__).resolve().parents[2] / "scenarios"
    before = (root / "skills" / skill / "SKILL.md").read_text().splitlines(keepends=True)
    after = (root / str(k) / "edit" / skill / "SKILL.md").read_text().splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(before, after, f"a/{skill}/SKILL.md", f"b/{skill}/SKILL.md")
    )


async def _simulate(k: int, tmp: Path, client: ModelClient) -> tuple[list[PairVerdict], str]:
    skill = SKILL[k]
    selected = [t for t in golden_tasks() if t["expected_skill"] in (skill, None)]
    pairs: list[PairVerdict] = []
    for task in selected:
        g_run = await run(task, world=world_for(tmp))
        golden = to_trace(g_run, f"g_{task['task_id']}", label=str(task["task_label"]))
        facts = await bless.extract_facts(golden, client)
        golden = golden.model_copy(
            update={
                "success_criteria": await bless.infer_criteria(golden, client),
                "world_facts": facts,
                "world_dependent": bool(facts) or bless.uses_world_tools(golden),
            }
        )
        r_run = await run(
            task, world=world_for(tmp, overlay=k == 3), scenario=k, blocked=cassette_blocker(g_run)
        )
        replay = to_trace(r_run, f"r_{task['task_id']}", kind="replay", golden_of=golden.run_id)
        unexpected = unexpected_calls(replay)
        finding = level0.diff(golden, replay, unexpected)
        ctx = PairContext(golden, replay, unexpected, scenario_diff(k, skill), finding)
        pair = await judge_pair(ctx, client)
        pair = await check_pair(pair, golden, replay, client, FixtureTavily(TAVILY_SCENARIO3))
        pairs.append(pair)
    return pairs, skill


async def _verdict(k: int, tmp: Path, settings: Settings) -> tuple[list[PairVerdict], str, str]:
    async with model_client(settings) as client:
        pairs, skill = await _simulate(k, tmp, client)
        result = decide(pairs, n_failed=0, n_total=len(pairs), ultra_timed_out=False)
        inp = NarrativeInput(
            decision=result.decision,
            action=result.action,
            skills=[skill],
            pairs=pairs,
            cost_by_tier={"nano": 0.002, "super": 0.006, "ultra": 0.03},
            reasons=result.reasons,
        )
        narrative = await write_narrative(inp, client)
        assert narrative.fallback_used is False and validate_narrative(narrative.text, inp)
        return pairs, result.decision, narrative.headline


def _outcomes(pairs: list[PairVerdict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pair in pairs:
        counts[pair.outcome] = counts.get(pair.outcome, 0) + 1
    return counts


async def test_scenario1_blocks_on_level0_close_issue_in_6_of_14(
    tmp_path: Path, settings: Settings
) -> None:
    pairs, decision, headline = await _verdict(1, tmp_path, settings)
    assert len(pairs) == 14
    assert _outcomes(pairs) == {"regression": 6, "pass": 8}
    assert all(p.decided_by == "level0" for p in pairs if p.outcome == "regression")
    assert all(
        c.tool == "github.close_issue"
        for p in pairs
        for c in p.level0.new_calls
        if p.level0.hard_fail
    )
    assert decision == "BLOCK"
    assert headline == "Blocked: the new github-triage would close issues in 6 of 14 replays."


async def test_scenario2_blocks_on_the_dropped_vat_line_in_3_of_10(
    tmp_path: Path, settings: Settings
) -> None:
    pairs, decision, headline = await _verdict(2, tmp_path, settings)
    assert len(pairs) == 10
    assert _outcomes(pairs) == {"regression": 3, "pass": 7}
    for pair in pairs:
        if pair.outcome == "regression":
            assert [o.tier for o in pair.opinions] == ["nano", "super", "ultra"]
            assert {o.verdict for o in pair.opinions} == {"regressed"}
            assert not pair.level0.hard_fail
    assert decision == "BLOCK"
    assert headline == "Blocked: expense-report now drops the VAT line on 3 of 10 past tasks."


async def test_scenario3_passes_with_drift_on_4_of_7(tmp_path: Path, settings: Settings) -> None:
    pairs, decision, headline = await _verdict(3, tmp_path, settings)
    assert len(pairs) == 7
    assert _outcomes(pairs) == {"drift": 4, "pass": 3}
    for pair in pairs:
        if pair.outcome == "drift":
            (check,) = pair.drift
            assert (check.old_value, check.new_value, check.attributable) == (
                "1.0800",
                "1.1630",
                True,
            )
    assert decision == "PASS"
    assert headline == "Passed: 7 tasks checked; 4 changed because the world did, not the skill."
