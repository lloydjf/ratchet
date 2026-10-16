"""SkillRegress-Bench metrics: κ, recall, false blocks, drift, pairs, cost."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_fixtures import edit, op, pair, small_edits  # noqa: E402

from ratchet.bench import metrics  # noqa: E402
from ratchet.bench.metrics import Rate  # noqa: E402


def test_cohen_kappa_matches_the_textbook_two_by_two_example() -> None:
    # Wikipedia "Cohen's kappa" example: 50 proposals, a=20 yes/yes, b=5 yes/no,
    # c=10 no/yes, d=15 no/no → p_o = 0.70, p_e = 0.50, κ = 0.40.
    a = ["yes"] * 20 + ["yes"] * 5 + ["no"] * 10 + ["no"] * 15
    b = ["yes"] * 20 + ["no"] * 5 + ["yes"] * 10 + ["no"] * 15
    assert metrics.cohen_kappa(a, b) == pytest.approx(0.40)


def test_cohen_kappa_perfect_agreement_chance_and_degenerate_cases() -> None:
    assert metrics.cohen_kappa(["a", "b", "a"], ["a", "b", "a"]) == pytest.approx(1.0)
    # Systematic disagreement on balanced labels is below chance.
    assert metrics.cohen_kappa(["a", "b"], ["b", "a"]) == pytest.approx(-1.0)
    assert metrics.cohen_kappa([], []) is None
    assert metrics.cohen_kappa(["a", "a"], ["a", "a"]) is None  # p_e = 1: undefined
    with pytest.raises(ValueError):
        metrics.cohen_kappa(["a"], ["a", "b"])


def test_percentile_interpolates_and_handles_empty() -> None:
    assert metrics.percentile([], 95) is None
    assert metrics.percentile([5.0], 95) == 5.0
    assert metrics.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert metrics.percentile([0.0, 10.0], 95) == pytest.approx(9.5)


def test_rate_value_is_none_without_items() -> None:
    assert Rate(num=0, den=0).value is None
    assert Rate(num=1, den=4).value == 0.25


def test_detection_recall_counts_block_and_warn_and_excludes_errored_edits() -> None:
    edits = small_edits()
    recall = metrics.detection_recall(edits)
    assert (recall.num, recall.den) == (3, 4)  # r2 BLOCK, r4 BLOCK, r1 WARN; r5 missed; r3 errored
    block = metrics.block_recall(edits)
    assert (block.num, block.den) == (2, 4)
    by_type = metrics.recall_by_type(edits)
    assert by_type["R5"].value == 0.0 and by_type["R3"].den == 0 and by_type["R1"].value == 1.0


def test_false_block_and_false_warn_rates_use_the_controls() -> None:
    edits = small_edits()
    fb = metrics.false_block_rate(edits)
    assert (fb.num, fb.den) == (1, 4)  # d1-b blocked out of n0-a, n0-b, d1-a, d1-b
    assert metrics.false_block_rate(edits, ["N0"]).num == 0
    fw = metrics.false_warn_rate(edits)
    assert (fw.num, fw.den) == (1, 2)


def test_drift_accuracy_needs_every_affected_pair_to_be_drift() -> None:
    edits = small_edits()
    acc = metrics.drift_accuracy(edits)
    assert (acc.num, acc.den) == (1, 2)
    no_labels = edit("d1-c", "D1", "PASS", [pair("t9", "pass")])
    assert metrics.drift_accuracy([no_labels]).den == 0


def test_pair_precision_recall_treat_uncertain_as_flagged() -> None:
    precision, recall = metrics.pair_precision_recall(small_edits())
    # expected regression: t1 (flag), t3 (flag), t4 (missed), t5 (uncertain → flag)
    assert (recall.num, recall.den) == (3, 4)
    # flagged: t1, t3, t5 (true) + n0-b t2 uncertain, d1-b t7 regression (false)
    assert (precision.num, precision.den) == (3, 5)


def test_cost_and_latency_stats_are_means_and_p95_over_verdicts() -> None:
    by_tier, total, counterfactual = metrics.cost_stats(small_edits())
    assert total.n == 8 and total.mean is not None
    assert total.mean == pytest.approx((0.001 * 6 + 0.004 + 0.0005) / 8)
    assert counterfactual.mean == pytest.approx(total.mean * 3)
    assert by_tier["ultra"].mean == pytest.approx(total.mean / 2)
    assert metrics.latency_stats(small_edits()).mean == pytest.approx(10.0)


def test_kappa_by_tier_rates_each_tier_against_the_reference() -> None:
    edits = small_edits()
    reference = metrics.construction_reference(edits)
    kappas = metrics.kappa_by_tier(edits, reference)
    assert kappas["nano"].n == 8  # every pair with a nano opinion
    assert kappas["super"].n == 2 and kappas["ultra"].n == 2
    assert kappas["nano"].value is not None
    assert metrics.kappa_by_tier(edits, {})["nano"] == metrics.Kappa(n=0, value=None)


def test_opinion_and_outcome_flags_are_binary() -> None:
    assert metrics.opinion_flag("regressed") == "flag" == metrics.opinion_flag("uncertain")
    assert metrics.opinion_flag("equivalent") == "ok" == metrics.opinion_flag("improved")
    assert metrics.outcome_flag("regression") == "flag"
    assert metrics.outcome_flag("drift") == "ok" == metrics.outcome_flag("pass")


def test_noise_floor_is_the_pass_share_of_unchanged_pairs() -> None:
    noise = [
        edit("noise-1", "N0", "PASS", [pair("a", "pass"), pair("b", "improved")]),
        edit("noise-2", "N0", "WARN", [pair("a", "uncertain"), pair("b", "failed")]),
    ]
    floor = metrics.noise_floor(noise)
    assert floor.n_pairs == 3 and floor.pass_rate == pytest.approx(2 / 3)
    assert metrics.noise_floor([]).pass_rate is None


def test_summarize_fills_every_metric() -> None:
    m = metrics.summarize(small_edits(), noise=small_edits()[:1])
    assert m.n_edits == 9 and m.n_errors == 1
    assert m.recall.value == 0.75 and m.false_block.value == 0.25
    assert m.noise is not None and m.noise.n_pairs == 2
    assert set(m.recall_by_type) == {"R1", "R2", "R3", "R4", "R5", "R6"}
    assert m.kappa_human["nano"].n == 0
    assert op("nano", "equivalent").tier == "nano"


def test_noise_floor_reports_the_share_of_control_diverged_pairs() -> None:
    from ratchet.bench.metrics import noise_floor
    from ratchet.bench.results import EditResult, PairResult

    pairs = [
        PairResult(task_id="a", golden_run_id="g", replay_run_id="r", outcome="pass"),
        PairResult(
            task_id="b",
            golden_run_id="g",
            replay_run_id="r",
            outcome="pass",
            control_outcome="diverges",
        ),
        PairResult(task_id="c", golden_run_id="g", replay_run_id="r", outcome="uncertain"),
        PairResult(task_id="d", golden_run_id="g", replay_run_id="r", outcome="failed"),
    ]
    edit = EditResult(
        bench_id="noise-x-1", skill="x", type="N0", expected_decision="PASS", pairs=pairs
    )
    floor = noise_floor([edit])
    assert floor.n_pairs == 3 and floor.pass_rate == 2 / 3 and floor.noisy_rate == 1 / 3


def test_drift_accuracy_forgives_a_labeled_pair_that_did_not_differ_this_time() -> None:
    """A D1 edit passes with drift: the labeled pairs that differed are `drift`, one happened
    to match the golden (`pass`). That is a hit; a `uncertain` among them, or a WARN, is not."""
    hit = edit(
        "d1-x",
        "D1",
        "PASS",
        [pair("t1", "drift", "drift"), pair("t2", "pass", "drift"), pair("t3", "pass")],
    )
    unsure = edit(
        "d1-y",
        "D1",
        "WARN",
        [pair("t1", "drift", "drift"), pair("t2", "uncertain", "drift")],
    )
    none_showed = edit("d1-z", "D1", "PASS", [pair("t1", "pass", "drift")])
    acc = metrics.drift_accuracy([hit, unsure, none_showed])
    assert (acc.num, acc.den) == (1, 3)
