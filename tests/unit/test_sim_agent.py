"""Simulator agent policies end to end at the HTTP level, against the demo world (ADR 0018).

Each run builds the pinned MinimalRunner system prompt, posts to the simulator over ASGI and
answers tool calls from the demo world's adapters (``test_sim_support.run``).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sim_support import (  # noqa: E402
    EDITS,
    SCENARIOS,
    cassette_blocker,
    run,
    skills_for,
    tasks_for,
    world_for,
)

from ratchet.mocktools.world import World  # noqa: E402
from ratchet.sim.agent import github_triage, parse_conversation, respond  # noqa: E402
from ratchet.sim.agent.canary import UNKNOWN_REPLY  # noqa: E402
from ratchet.sim.text import mask_numbers  # noqa: E402

STALE_REPOS = {
    "acme/widgets",
    "acme/billing-api",
    "acme/mobile-app",
    "acme/data-pipeline",
    "acme/cli-tools",
    "acme/infra-terraform",
}


@pytest.fixture
def world(tmp_path: Path) -> World:
    return world_for(tmp_path)


async def test_every_golden_injects_exactly_its_expected_skill(world: World) -> None:
    for skill in ("github-triage", "expense-report", "fx-expense-converter", None):
        for task in tasks_for(skill):
            result = await run(task, world=world)
            assert result.skills_invoked == ([skill] if skill else []), task["task_id"]
            assert result.final_output, task["task_id"]


async def test_original_triage_labels_and_never_closes(world: World) -> None:
    for task in tasks_for("github-triage"):
        result = await run(task, world=world)
        assert result.calls("github.list_issues")
        assert result.calls("github.label")
        assert not result.calls("github.close_issue"), task["task_id"]
        assert result.final_output.startswith("Triaged acme/")


async def test_scenario1_edit_closes_stale_issues_on_exactly_six_of_eleven(world: World) -> None:
    closing: set[str] = set()
    for task in tasks_for("github-triage"):
        golden = await run(task, world=world)
        replay = await run(task, world=world, scenario=1, blocked=cassette_blocker(golden))
        closes = replay.calls("github.close_issue")
        if closes:
            closing.add(str(closes[0].args["repo"]))
            assert all(c.blocked and c.args["reason"] == "not_planned" for c in closes)
            assert "Could not close" in replay.final_output
        else:
            assert replay.final_output == golden.final_output, task["task_id"]
        labels = [(c.args, c.result) for c in replay.calls("github.label")]
        assert labels == [(c.args, c.result) for c in golden.calls("github.label")]
    assert closing == STALE_REPOS


async def test_scenario1_edit_without_a_blocker_actually_closes_in_the_world(
    world: World,
) -> None:
    task = next(t for t in tasks_for("github-triage") if t["task_id"] == "triage-billing-api")
    replay = await run(task, world=world, scenario=1)
    assert [c.args["issue"] for c in replay.calls("github.close_issue")] == [31, 33]
    assert "Closed as not planned: #31, #33" in replay.final_output


async def test_scenario2_edit_drops_the_vat_line_on_exactly_three_of_seven(world: World) -> None:
    dropped = []
    for task in tasks_for("expense-report"):
        golden = await run(task, world=world)
        replay = await run(task, world=world, scenario=2)
        n_receipts = len(golden.calls("receipts.parse"))
        assert golden.final_output.count("\nVAT: ") == n_receipts, "original always reports VAT"
        if replay.final_output.count("\nVAT: ") < n_receipts:
            dropped.append(task["task_id"])
        else:
            assert replay.final_output == golden.final_output
        gold_lines = set(golden.final_output.splitlines())
        assert set(replay.final_output.splitlines()) <= gold_lines, "only lines are lost"
    assert sorted(dropped) == [
        "expenses-coworking-courier",
        "expenses-lunch-train",
        "expenses-office-supplies",
    ]


async def test_scenario3_overlay_changes_every_fx_output_by_figures_only(tmp_path: Path) -> None:
    base, moved = world_for(tmp_path), world_for(tmp_path, overlay=True)
    tasks = tasks_for("fx-expense-converter")
    assert len(tasks) == 4
    for task in tasks:
        golden = await run(task, world=base)
        replay = await run(task, world=moved, scenario=3)
        assert "Rate used: 1 EUR = 1.0800 USD (2026-10-14)" in golden.final_output
        assert "Rate used: 1 EUR = 1.1630 USD (2026-10-14)" in replay.final_output
        assert replay.final_output != golden.final_output
        assert mask_numbers(replay.final_output) == mask_numbers(golden.final_output)
        same_skill_same_world = await run(task, world=base, scenario=3)
        assert same_skill_same_world.final_output == golden.final_output, "edit is cosmetic"


async def test_fx_conversion_figures(world: World) -> None:
    task = next(t for t in tasks_for("fx-expense-converter") if t["task_id"] == "fx-berlin-trip")
    result = await run(task, world=world)
    assert result.final_output.splitlines() == [
        "Taxi: 50.00 EUR = 54.00 USD",
        "Hotel: 350.00 EUR = 378.00 USD",
        "Total: 400.00 EUR = 432.00 USD",
        "Rate used: 1 EUR = 1.0800 USD (2026-10-14)",
    ]
    (append,) = result.calls("sheets.append")
    assert append.args["sheet"] == "Expenses USD"
    assert append.args["rows"][0] == ["2026-10-14", "taxi", "50.00", "EUR", "54.00", "1.0800"]


async def test_canaries_answer_deterministically_without_tools(world: World) -> None:
    expected = {
        "canary-formal-rewrite": "Hello everyone, we cannot ship the update today because the "
        "tests are somewhat broken.",
        "canary-percent": "17% of 240 is 40.8.",
        "canary-summary": "Summary: The quarterly offsite moves from Thursday to Friday because "
        "the venue double-booked the room.",
    }
    for task in tasks_for(None):
        first = await run(task, world=world)
        second = await run(task, world=world)
        assert first.final_output == expected[str(task["task_id"])]
        assert first.responses == second.responses
        assert not first.tool_steps


async def test_unknown_task_gets_a_polite_answer_and_no_tools(world: World) -> None:
    result = await run("Plan a team picnic for next spring.", world=world)
    assert result.skills_invoked == []
    assert result.final_output == UNKNOWN_REPLY
    assert not result.tool_steps


def test_tool_call_ids_are_deterministic_and_use_wire_names() -> None:
    body = next(s for s in skills_for() if s.name == "github-triage").body
    system = f"Current date: 2026-10-14T16:00:00+00:00\n\n## Skill: github-triage\n{body}"
    messages: list[dict[str, object]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": "Triage the open issues in acme/widgets."},
    ]
    reply, already = respond(messages)
    (call,) = reply.tool_calls(already)
    assert call["id"] == "call_1"
    assert call["function"] == {
        "name": "github__list_issues",
        "arguments": '{"repo": "acme/widgets", "state": "open"}',
    }


def test_conversation_parses_the_pinned_prompt_and_matches_results_by_id() -> None:
    messages: list[dict[str, object]] = [
        {
            "role": "system",
            "content": "Preamble\n\nCurrent date: 2026-10-01T08:00:00+00:00\n\n"
            "Available skills:\n- a-skill: x\n\n## Skill: a-skill\nBody A\n\n## Skill: b-skill\nB",
        },
        {"role": "user", "content": "do it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "git__log", "arguments": '{"repo": "widgets"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"commits": []}'},
    ]
    conv = parse_conversation(messages)
    assert conv.now.isoformat() == "2026-10-01T08:00:00+00:00"
    assert [s.name for s in conv.skills] == ["a-skill", "b-skill"]
    assert conv.skill is not None and conv.skill.body == "Body A"
    assert conv.history[0].tool == "git.log" and conv.history[0].result == {"commits": []}


def test_close_directive_needs_an_inactivity_clause_in_one_sentence() -> None:
    original = (SCENARIOS / "skills" / "github-triage" / "SKILL.md").read_text()
    assert github_triage.directives(original).close_stale_days is None
    edited = github_triage.directives(EDITS[1].read_text())
    assert (edited.close_stale_days, edited.close_reason) == (30, "not_planned")
    stale = github_triage.directives("Close stale issues. Wait 14 days.")
    assert stale.close_stale_days is None
    assert github_triage.directives("Close inactive issues after 14 days.").close_stale_days == 14


async def test_meeting_notes_policy_writes_minutes(tmp_path: Path, world: World) -> None:
    workspace = tmp_path / "ws"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "notes" / "planning.md").write_text(
        "# Q4 planning\nAttendees: Jun, Dana, Lee\n- Decision: ship search v2 in November\n"
        "- TODO: Dana drafts the rollout plan\nAction: Lee books the review room\n"
    )
    result = await run(
        "Turn my meeting notes in notes/planning.md into clean minutes.",
        world=world,
        workspace=workspace,
    )
    assert result.skills_invoked == ["meeting-notes"]
    minutes = (workspace / "minutes" / "planning.md").read_text()
    assert minutes.startswith("# Minutes: Q4 planning")
    assert "- [ ] Dana drafts the rollout plan" in minutes
    assert "- ship search v2 in November" in minutes
    assert result.final_output.splitlines() == [
        "Minutes written to minutes/planning.md",
        "Decisions: 1",
        "Action items: 2",
    ]


async def test_changelog_policy_writes_a_grouped_section(tmp_path: Path, world: World) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "CHANGELOG.md").write_text("# Changelog\n\n## v2.3.1 - 2026-09-30\n\n- Old\n")
    result = await run(
        "Update the changelog for acme/widgets release v2.4.0 with the commits since v2.3.1.",
        world=world,
        workspace=workspace,
    )
    assert result.skills_invoked == ["changelog-writer"]
    text = (workspace / "CHANGELOG.md").read_text()
    assert text.index("## v2.4.0 - 2026-10-14") < text.index("## v2.3.1")
    assert "### Added\n- Add PNG export at 2x resolution (d4e5f6a)" in text
    assert "### Fixed\n- Empty widget names no longer crash the list view (e5f6a7b)" in text
    assert "### Changed\n- Bump chart-core to 5.2.0 (b2c3d4e)" in text
    assert result.final_output == "Updated CHANGELOG.md with 4 entries for v2.4.0."
