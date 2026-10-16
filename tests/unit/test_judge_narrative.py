"""Narrative and digest: length and style constraints and the deterministic fallback."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import FakeModelClient, settings  # noqa: E402

from ratchet import clock, copy  # noqa: E402
from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge import narrative as nv  # noqa: E402
from ratchet.models import (  # noqa: E402
    CriterionCheck,
    JudgeOpinion,
    Level0Finding,
    PairVerdict,
    ToolCall,
    Verdict,
)


def _call(tool: str, side_effect: str) -> ToolCall:
    return ToolCall(
        step_id="t003", tool=tool, args_norm={}, args_hash="h", result_hash=None,
        side_effect=side_effect, source="cassette", cassette_key=None,
    )  # fmt: skip


def _pair(outcome: str, *, decided_by: str = "ultra", hard: bool = False) -> PairVerdict:
    new = [_call("github.close_issue", "write_irreversible")] if hard else []
    ops = [
        JudgeOpinion(
            tier="ultra", verdict="regressed", confidence=0.9, reasons=["VAT dropped"],
            evidence_steps=["t002"], usd=0.02,
            criteria_met=[CriterionCheck(criterion="Every row has its VAT line", met=False)],
        )
    ] if outcome == "regression" and not hard else []  # fmt: skip
    return PairVerdict(
        golden_run_id="g", replay_run_id="r",
        level0=Level0Finding(golden_run_id="g", replay_run_id="r", new_calls=new,
                             missing_calls=[], changed_args=[], hard_fail=hard),
        opinions=ops, drift=[], outcome=outcome, usd=0.02, decided_by=decided_by,
        task_label="October expenses", reason="Every row has its VAT line is no longer met.",
    )  # fmt: skip


def _inp(decision: str, pairs: list[PairVerdict], reasons: list[str] | None = None, **kw):  # type: ignore[no-untyped-def]
    action = {"BLOCK": "reverted", "WARN": "held", "PASS": "merged"}[decision]
    return nv.NarrativeInput(
        decision=decision, action=action, skills=["expense-report"], pairs=pairs,
        cost_by_tier={"nano": 0.004, "super": 0.03, "ultra": 0.05}, reasons=reasons or [], **kw,
    )  # fmt: skip


HEADLINES = [
    (
        _inp("BLOCK", [_pair("regression", decided_by="level0", hard=True), _pair("pass")]),
        "Blocked: the new expense-report would close issues in 1 of 2 replays.",
    ),
    (
        _inp("BLOCK", [_pair("regression"), _pair("pass")]),
        'Blocked: expense-report now fails "Every row has its VAT line" on 1 of 2 past tasks.',
    ),
    (
        _inp("WARN", [_pair("uncertain"), _pair("pass")], ["judges_disagreed"]),
        "Held: judges disagreed on 1 of 2 replays. Review before merging.",
    ),
    (_inp("PASS", [_pair("pass")] * 3), "Passed: expense-report still handles all 3 past tasks."),
    (
        _inp("PASS", [_pair("pass"), _pair("drift")], ["drift"]),
        "Passed: 2 tasks checked; 1 changed because the world did, not the skill.",
    ),
    (
        _inp("PASS", [], ["no_affected_traces"], n_total=0),
        "Passed: no blessed tasks use expense-report yet. Bless one to protect it.",
    ),
]


@pytest.mark.parametrize(("inp", "headline"), HEADLINES)
def test_deterministic_headlines_follow_the_headline_templates(
    inp: nv.NarrativeInput, headline: str
) -> None:
    assert nv.headline_for(inp) == headline
    text = nv.fallback_narrative(inp)
    assert text.startswith(headline)
    assert nv.validate_narrative(text, inp)
    assert len(text.split()) <= copy.NARRATIVE_MAX_WORDS


@pytest.mark.parametrize(
    ("inp", "closing"),
    [
        (_inp("BLOCK", [_pair("regression")]), copy.CLOSING_NOTHING),
        (_inp("WARN", [_pair("uncertain")], ["uncertain"]), copy.CLOSING_APPROVE),
        (_inp("WARN", [_pair("regression")], ["single_judge_regression"]), copy.CLOSING_ROLL_BACK),
        (_inp("PASS", [_pair("drift")], ["drift"]), copy.CLOSING_REBLESS),
        (_inp("PASS", [_pair("pass")]), copy.CLOSING_NOTHING),
    ],
)
def test_closing_sentence_matches_the_next_step(inp: nv.NarrativeInput, closing: str) -> None:
    assert nv.closing_for(inp) == closing
    assert nv.fallback_narrative(inp).endswith(closing)


GOOD = (
    "Blocked: expense-report now drops the VAT line on 1 of 2 past tasks. Ratchet replayed "
    "two blessed tasks; Super and Ultra agreed the October expenses run lost its VAT column. "
    "Nothing to do."
)


async def test_valid_ultra_narrative_is_used(tmp_path: Path) -> None:
    inp = _inp("BLOCK", [_pair("regression"), _pair("pass")])
    client = FakeModelClient().add("narrative", "<think>plan</think>" + GOOD)
    with settings(tmp_path):
        result = await nv.write_narrative(inp, client)
    assert not result.fallback_used and result.text == GOOD
    assert result.headline == "Blocked: expense-report now drops the VAT line on 1 of 2 past tasks."
    call = client.calls[0]
    assert call.tier == "ultra" and call.thinking is False and call.max_tokens == 400
    assert call.purpose == "narrative" and call.response_format is None
    assert "Rules:" not in call.user_text and "October expenses" in call.user_text
    assert result.usd > 0


@pytest.mark.parametrize(
    "bad",
    [
        "The edit was blocked because VAT is missing. Nothing to do.",  # wrong first word
        "Blocked: the skill drops VAT. Nothing to do.",  # does not name the skill
        "Blocked: expense-report drops VAT. You should look at it.",  # wrong closing
        "Blocked: expense-report drops VAT.\n- item\nNothing to do.",  # bullet
        "Blocked: expense-report drops VAT! Nothing to do.",
        "Blocked: expense-report " + "word " * 190 + "Nothing to do.",  # too long
        "",
    ],
)
async def test_invalid_narratives_fall_back_to_the_template(tmp_path: Path, bad: str) -> None:
    inp = _inp("BLOCK", [_pair("regression"), _pair("pass")])
    with settings(tmp_path):
        result = await nv.write_narrative(inp, FakeModelClient().add("narrative", bad))
    assert result.fallback_used and result.headline == nv.headline_for(inp)
    assert result.text.startswith("Blocked: expense-report now fails")
    assert 'On "October expenses": Got worse.' in result.text


async def test_model_failure_falls_back(tmp_path: Path) -> None:
    inp = _inp("WARN", [_pair("uncertain")], ["judge_timeout"])
    client = FakeModelClient().add("narrative", UpstreamError("t", code="judge_timeout"))
    with settings(tmp_path):
        result = await nv.write_narrative(inp, client)
    assert result.fallback_used and result.usd == 0
    assert result.text.startswith("Held: the final judge timed out on 1 of 1 replays.")


def _verdict(decision: str, action: str, skill: str) -> Verdict:
    return Verdict(
        verdict_id=f"v_{skill}", edit_id="e", pairs=[], decision=decision, action=action,
        narrative="", cost={}, duration_s=1, created_at=clock.now(), affected_skills=[skill],
        headline=f"{copy.DECISION_WORDS[decision]}: {skill} headline.",
    )  # fmt: skip


DIGEST = nv.DigestInput(
    "2026-10-08",
    "2026-10-15",
    [
        _verdict("PASS", "merged", "changelog-writer"),
        _verdict("BLOCK", "reverted", "github-triage"),
        _verdict("WARN", "held", "expense-report"),
    ],
    {"ultra": 0.2},
)


async def test_digest_uses_valid_model_text(tmp_path: Path) -> None:
    text = "3 skill edits checked this week; 1 blocked. The agent got better at changelogs."
    with settings(tmp_path):
        result = await nv.write_digest(DIGEST, FakeModelClient().add("digest", text))
    assert not result.fallback_used and result.headline.startswith("3 skill edits")


async def test_digest_falls_back_with_counts(tmp_path: Path) -> None:
    with settings(tmp_path):
        result = await nv.write_digest(DIGEST, FakeModelClient().add("digest", "- bullets\n"))
    assert result.fallback_used
    assert result.text.startswith("3 skill edits checked this week; 1 blocked.")
    assert "changelog-writer" in result.text and "github-triage headline" in result.text
    assert len(result.text.split()) <= copy.DIGEST_MAX_WORDS
    failing = FakeModelClient().add("digest", UpstreamError("x"))
    with settings(tmp_path):
        assert (await nv.write_digest(DIGEST, failing)).fallback_used


def test_cascade_headline_prefers_short_failure_summary_over_free_text() -> None:
    from ratchet.judge.narrative import NarrativeInput, headline_for
    from ratchet.models import JudgeOpinion, Level0Finding, PairVerdict

    ultra = JudgeOpinion(
        tier="ultra",
        verdict="regressed",
        confidence=0.95,
        usd=0.0,
        evidence_steps=[],
        reasons=["A very long deliberation " * 20],
        failure_summary="drops the VAT line",
    )
    level0 = Level0Finding(
        golden_run_id="g",
        replay_run_id="r",
        new_calls=[],
        missing_calls=[],
        changed_args=[],
        hard_fail=False,
    )
    pair = PairVerdict(
        golden_run_id="g",
        replay_run_id="r",
        level0=level0,
        opinions=[ultra],
        drift=[],
        outcome="regression",
        usd=0.0,
        decided_by="ultra",
        reason="A very long deliberation " * 20,
    )
    inp = NarrativeInput(
        decision="BLOCK",
        action="reverted",
        skills=["expense-report"],
        pairs=[pair],
        cost_by_tier={"nano": 0.0, "super": 0.0, "ultra": 0.0},
        n_total=10,
    )
    assert (
        headline_for(inp) == "Blocked: expense-report now drops the VAT line on 1 of 10 past tasks."
    )


def test_level0_headline_names_the_hard_tool_seen_in_most_replays() -> None:
    from ratchet.judge.narrative import _dominant_hard_tool
    from ratchet.models import Level0Finding, PairVerdict, ToolCall

    def call(tool: str) -> ToolCall:
        return ToolCall(
            step_id="t1",
            tool=tool,
            args_norm={},
            args_hash=tool,
            result_hash=None,
            side_effect="write_irreversible",
            source="cassette",
            cassette_key=None,
        )

    def pair(*tools: str) -> PairVerdict:
        finding = Level0Finding(
            golden_run_id="g",
            replay_run_id="r",
            new_calls=[call(t) for t in tools],
            missing_calls=[],
            changed_args=[],
            hard_fail=True,
        )
        return PairVerdict(
            golden_run_id="g",
            replay_run_id="r",
            level0=finding,
            opinions=[],
            drift=[],
            outcome="regression",
            usd=0.0,
            decided_by="level0",
        )

    pairs = [pair("github.comment", "github.close_issue"), pair("github.close_issue")]
    assert _dominant_hard_tool(pairs) == "github.close_issue"
