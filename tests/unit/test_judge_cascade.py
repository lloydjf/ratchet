"""Cascade thresholds and order (including the rule on Ultra confirmation)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from judge_fakes import FakeModelClient, factory, opinion, pair_context, settings  # noqa: E402

from ratchet.errors import UpstreamError  # noqa: E402
from ratchet.judge.cascade import CascadeConfig, judge_pair  # noqa: E402


def _client(*items: tuple[str, str | BaseException]) -> FakeModelClient:
    client = FakeModelClient()
    for prompt, item in items:
        client.add(prompt, item)
    return client


async def _judge(name: str, client: FakeModelClient, tmp_path: Path, **cfg: object):  # type: ignore[no-untyped-def]
    with settings(tmp_path):
        return await judge_pair(pair_context(factory.by_name(name)), client, CascadeConfig(**cfg))  # type: ignore[arg-type]


async def test_level0_hard_fail_decides_without_any_model(tmp_path: Path) -> None:
    client = FakeModelClient()
    verdict = await _judge("r4_new_send", client, tmp_path)
    assert verdict.outcome == "regression" and verdict.decided_by == "level0"
    assert client.calls == [] and verdict.opinions == [] and verdict.usd == 0
    assert verdict.reason == "Checked without a model: 1 new external_send call(s)."
    assert verdict.task_label == "Standup notes" and verdict.task_input.startswith("Turn notes")


async def test_level0_off_sends_hard_fail_pairs_through_the_cascade(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("regressed", 0.95)),
        ("check", opinion("regressed", 0.9)),
        ("adjudicate", opinion("regressed", 0.9)),
    )
    verdict = await _judge("r4_new_send", client, tmp_path, level0=False)
    assert verdict.decided_by == "ultra" and client.tiers == ["nano", "super", "ultra"]
    assert "Level 0" not in client.calls[0].user_text


def _same_output(name: str) -> SimpleNamespace:
    """The named pair with the replay's final output equal to the golden's (ADR 0024: a
    paraphrased reply is a material difference and goes past Nano)."""
    pair = factory.by_name(name)
    replay = pair.replay.model_copy(update={"final_output": pair.golden.final_output})
    return SimpleNamespace(
        golden=pair.golden, replay=replay, unexpected=pair.unexpected, skill_diff=pair.skill_diff
    )


async def test_confident_nano_equivalent_passes_and_stops(tmp_path: Path) -> None:
    client = _client(("screen", opinion("equivalent", 0.85)))
    with settings(tmp_path):
        verdict = await judge_pair(
            pair_context(_same_output("n0_cosmetic")), client, CascadeConfig()
        )
    assert verdict.outcome == "pass" and verdict.decided_by == "nano"
    assert client.tiers == ["nano"] and verdict.usd > 0
    assert verdict.reason == "Output meets the criteria; differences are cosmetic."
    assert verdict.level0.output_similarity == 1.0


async def test_a_paraphrased_reply_cannot_be_cleared_by_nano_alone(tmp_path: Path) -> None:
    """ADR 0024: the n0_cosmetic fixture rewords its reply (similarity 0.53 < 0.90)."""
    client = _client(("screen", opinion("equivalent", 0.95)), ("check", opinion("equivalent", 0.9)))
    verdict = await _judge("n0_cosmetic", client, tmp_path)
    assert client.tiers == ["nano", "super"]
    assert verdict.outcome == "pass" and verdict.decided_by == "super"
    assert verdict.level0.output_similarity < 0.9


async def test_unsure_nano_equivalent_goes_to_super(tmp_path: Path) -> None:
    client = _client(("screen", opinion("equivalent", 0.84)), ("check", opinion("equivalent", 0.8)))
    verdict = await _judge("n0_cosmetic", client, tmp_path)
    assert verdict.outcome == "pass" and verdict.decided_by == "super"
    assert "A first-pass judge said: equivalent (0.84)." in client.calls[1].user_text


async def test_super_improved_is_improved(tmp_path: Path) -> None:
    client = _client(("screen", opinion("improved", 0.9)), ("check", opinion("improved", 0.82)))
    verdict = await _judge("n0_cosmetic", client, tmp_path)
    assert verdict.outcome == "improved" and verdict.reason.startswith("Meets the criteria")


async def test_nano_regressed_never_blocks_alone_and_super_regressed_goes_to_ultra(
    tmp_path: Path,
) -> None:
    client = _client(
        ("screen", opinion("regressed", 0.99)),
        ("check", opinion("regressed", 0.95, evidence=["t003"])),
        ("adjudicate", opinion("regressed", 0.9, evidence=["t003"])),
    )
    verdict = await _judge("r1_logic", client, tmp_path)
    assert client.tiers == ["nano", "super", "ultra"]
    assert verdict.outcome == "regression" and verdict.decided_by == "ultra"
    ultra = client.calls[2]
    assert ultra.thinking is True and ultra.reasoning_budget == 4000
    assert "- nano: <<<DATA id=" in ultra.user_text  # model opinions are data too
    assert "\nregressed (0.99)" in ultra.user_text


async def test_ultra_contradicting_confident_super_is_contested(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("regressed", 0.9)),
        ("check", opinion("regressed", 0.85)),
        ("adjudicate", opinion("equivalent", 0.9)),
    )
    verdict = await _judge("r1_logic", client, tmp_path)
    assert verdict.outcome == "uncertain"
    assert verdict.reason.startswith("Judges disagreed") and verdict.reason.endswith(
        "Held for a human."
    )


async def test_ultra_is_final_when_super_was_unsure(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("uncertain", 0.5)),
        ("check", opinion("regressed", 0.6)),
        ("adjudicate", opinion("equivalent", 0.9)),
    )
    verdict = await _judge("r3_changed_label", client, tmp_path)
    assert verdict.outcome == "pass" and verdict.decided_by == "ultra"


async def test_ultra_uncertain_is_uncertain(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("uncertain", 0.5)),
        ("check", opinion("uncertain", 0.5)),
        ("adjudicate", opinion("uncertain", 0.4, ["The rate may have moved"])),
    )
    verdict = await _judge("d1_fx_drift", client, tmp_path)
    assert verdict.outcome == "uncertain" and verdict.reason == (
        "The rate may have moved. Held for a human."
    )


async def test_ultra_timeout_makes_the_pair_uncertain_with_judge_timeout(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("regressed", 0.9)),
        ("check", opinion("regressed", 0.9)),
        ("adjudicate", UpstreamError("ultra call timed out", code="judge_timeout")),
    )
    verdict = await _judge("r1_logic", client, tmp_path)
    assert verdict.outcome == "uncertain" and verdict.error_code == "judge_timeout"
    assert verdict.opinions[-1].reasons == ["judge_timeout"]
    assert "Final judge timed out" in verdict.reason


async def test_failed_super_call_is_uncertain_and_the_pair_moves_on(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("regressed", 0.9)),
        ("check", UpstreamError("503", code="upstream_unavailable")),
        ("adjudicate", opinion("regressed", 0.9)),
    )
    verdict = await _judge("r1_logic", client, tmp_path)
    sup = verdict.opinions[1]
    assert sup.tier == "super" and sup.verdict == "uncertain"
    assert sup.reasons == ["upstream_unavailable"]
    assert verdict.outcome == "regression"


async def test_missing_hard_call_removes_the_nano_shortcut(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("equivalent", 0.95)),
        ("check", opinion("regressed", 0.9, evidence=["t009"])),
        ("adjudicate", opinion("regressed", 0.9)),
    )
    verdict = await _judge("r6_underfire", client, tmp_path)
    assert client.tiers == ["nano", "super", "ultra"]
    assert "Level 0 (deterministic): missing github.label" in client.calls[0].user_text
    assert verdict.outcome == "regression"


async def test_changed_args_remove_the_nano_shortcut(tmp_path: Path) -> None:
    """ADR 0024: a Level 0 structural finding of any class sends the pair past Nano."""
    client = _client(
        ("screen", opinion("equivalent", 0.95)),
        ("check", opinion("regressed", 0.9, evidence=["t002"])),
        ("adjudicate", opinion("regressed", 0.9, evidence=["t002"])),
    )
    verdict = await _judge("r3_changed_label", client, tmp_path)
    assert client.tiers == ["nano", "super", "ultra"]
    assert verdict.outcome == "regression" and verdict.level0.changed_args


async def test_invented_evidence_steps_are_dropped_and_noted(tmp_path: Path) -> None:
    check = (
        '{"verdict": "regressed", "confidence": 0.9, "reasons": ["wrong label"],'
        ' "evidence_steps": ["t002", "t042"], "criteria_met": [{"criterion": "#12 labeled bug",'
        ' "met": false, "evidence_steps": ["t002", "x9"]}]}'
    )
    client = _client(
        ("screen", opinion("uncertain", 0.5)),
        ("check", check),
        ("adjudicate", opinion("regressed", 0.9, evidence=["t002"])),
    )
    verdict = await _judge("r3_changed_label", client, tmp_path)
    sup = verdict.opinions[1]
    assert sup.evidence_steps == ["t002"]
    assert sup.criteria_met[0].evidence_steps == ["t002"]
    assert "t042" in sup.reasons[-1] and "x9" in sup.reasons[-1]
    assert verdict.reason == "#12 labeled bug is no longer met."


async def test_harness_crash_is_a_failed_pair_without_judges(tmp_path: Path) -> None:
    pair = factory.by_name("n0_cosmetic")
    crashed = pair.replay.model_copy(update={"harness_ok": False, "error_code": "harness_failed"})
    client = FakeModelClient()
    with settings(tmp_path):
        ctx = pair_context(pair)
        ctx = type(ctx)(ctx.golden, crashed, ctx.unexpected, ctx.skill_diff, ctx.level0)
        verdict = await judge_pair(ctx, client)
    assert verdict.outcome == "failed" and verdict.decided_by == "infra"
    assert verdict.reason == "Agent crashed during replay." and client.calls == []


@pytest.mark.parametrize(
    ("mode", "prompt", "tier"),
    [("nano_only", "screen", "nano"), ("super_only", "check", "super"),
     ("ultra_only", "adjudicate", "ultra")],
)  # fmt: skip
async def test_single_tier_modes_call_exactly_one_judge(
    tmp_path: Path, mode: str, prompt: str, tier: str
) -> None:
    client = _client((prompt, opinion("regressed", 0.7)))
    verdict = await _judge("r1_logic", client, tmp_path, mode=mode)
    assert client.tiers == [tier] and verdict.decided_by == tier
    assert verdict.outcome == "regression"


async def test_single_tier_low_confidence_equivalent_is_uncertain(tmp_path: Path) -> None:
    client = _client(("screen", opinion("equivalent", 0.6)))
    verdict = await _judge("n0_cosmetic", client, tmp_path, mode="nano_only")
    assert verdict.outcome == "uncertain"
    client = _client(("adjudicate", opinion("improved", 0.6)))
    assert (await _judge("n0_cosmetic", client, tmp_path, mode="ultra_only")).outcome == (
        "improved"
    )


async def test_screen_gets_call_summaries_and_check_gets_step_previews(tmp_path: Path) -> None:
    client = _client(
        ("screen", opinion("uncertain", 0.5)),
        ("check", opinion("equivalent", 0.9)),
    )
    await _judge("r5_overfire", client, tmp_path)
    screen, check = client.calls
    assert "t002 github.label (write_reversible)" in screen.user_text
    assert '"labels":["bug"]' not in screen.user_text  # no args at Level 1
    assert "t002 | tool github.label (write_reversible)" in check.user_text
    assert '"labels":["bug"]' in check.user_text
    assert "Use for any planning task." in check.user_text  # skill diff
    assert "10:00 Design" in check.user_text  # result preview


async def test_on_opinion_reports_each_tier_as_it_answers(tmp_path: Path) -> None:
    from ratchet.models import JudgeOpinion

    client = _client(
        ("screen", opinion("regressed", 0.99)),
        ("check", opinion("regressed", 0.95, evidence=["t003"])),
        ("adjudicate", opinion("regressed", 0.9, evidence=["t003"])),
    )
    seen: list[tuple[str, int]] = []

    async def hook(op: JudgeOpinion) -> None:
        seen.append((op.tier, len(client.calls)))  # calls made so far when the hook fires

    with settings(tmp_path):
        verdict = await judge_pair(
            pair_context(factory.by_name("r1_logic")), client, CascadeConfig(), on_opinion=hook
        )
    assert seen == [("nano", 1), ("super", 2), ("ultra", 3)]
    assert [o.tier for o in verdict.opinions] == ["nano", "super", "ultra"]


async def test_on_opinion_is_not_called_when_level0_decides(tmp_path: Path) -> None:
    from ratchet.models import JudgeOpinion

    seen: list[JudgeOpinion] = []

    async def hook(op: JudgeOpinion) -> None:
        seen.append(op)

    with settings(tmp_path):
        await judge_pair(
            pair_context(factory.by_name("r4_new_send")), FakeModelClient(), on_opinion=hook
        )
    assert seen == []


def test_clean_reasons_drops_deliberation_and_caps_length() -> None:
    from ratchet.judge.cascade import clean_reasons

    long = "The VAT line is missing for the second receipt. " * 10
    out = clean_reasons(["Wait, re-evaluating: the golden run is the reference.", long, "  "])
    assert out[0].startswith("The VAT line is missing") and len(out[0]) <= 241
    assert len(out) == 1
    assert clean_reasons(["Hmm, only thinking here"]) == ["Hmm, only thinking here"]  # never empty
    assert clean_reasons([]) == []


def test_short_failure_accepts_only_short_plain_phrases() -> None:
    from ratchet.judge.cascade import short_failure

    assert short_failure("Drops the VAT line.") == "drops the VAT line"
    assert short_failure("") is None
    assert short_failure("one two three four five six seven eight nine ten eleven") is None
    assert short_failure('fails "x": because') is None


async def test_ultra_regressed_without_a_causal_diff_is_uncertain_not_regression(
    tmp_path: Path,
) -> None:
    """A difference the diff cannot explain is nondeterminism → held, never reverted
    (in a live noise run, 8/8 unchanged-skill replays were blocked before this rule)."""
    client = _client(
        ("screen", opinion("regressed", 0.95)),
        ("check", opinion("regressed", 0.95, evidence=["t003"])),
        ("adjudicate", opinion("regressed", 0.9, evidence=["t003"], caused_by_diff=False)),
    )
    verdict = await _judge("r1_logic", client, tmp_path)
    assert verdict.outcome == "uncertain" and verdict.decided_by == "ultra"
    assert "nondeterminism" in verdict.reason
    assert verdict.opinions[-1].caused_by_diff is False
