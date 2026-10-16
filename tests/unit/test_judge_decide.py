"""Every row of the agreement table produces the stated decision."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ratchet.judge import decide as d
from ratchet.models import JudgeOpinion, Level0Finding, PairVerdict


def _op(tier: str, verdict: str, confidence: float = 0.9) -> JudgeOpinion:
    return JudgeOpinion(
        tier=tier, verdict=verdict, confidence=confidence, reasons=["r"], evidence_steps=[],
        usd=0.001,
    )  # fmt: skip


def _pair(
    outcome: str,
    opinions: list[JudgeOpinion] | None = None,
    *,
    decided_by: str = "nano",
    hard_fail: bool = False,
    missing_hard: bool = False,
    error_code: str | None = None,
) -> PairVerdict:
    return PairVerdict(
        golden_run_id="g",
        replay_run_id="r",
        level0=Level0Finding(
            golden_run_id="g", replay_run_id="r", new_calls=[], missing_calls=[], changed_args=[],
            hard_fail=hard_fail, missing_hard=missing_hard,
        ),
        opinions=opinions or [],
        drift=[],
        outcome=outcome,
        usd=0.0,
        decided_by=decided_by,
        error_code=error_code,
    )  # fmt: skip


PASS = _pair("pass", [_op("nano", "equivalent")])
AGREED = _pair(
    "regression",
    [_op("nano", "regressed"), _op("super", "regressed"), _op("ultra", "regressed")],
    decided_by="ultra",
)
ULTRA_ONLY = _pair(
    "regression",
    [_op("nano", "uncertain"), _op("super", "uncertain"), _op("ultra", "regressed")],
    decided_by="ultra",
)
MISSING_HARD = _pair(
    "regression",
    [_op("nano", "equivalent"), _op("super", "equivalent", 0.5), _op("ultra", "regressed")],
    decided_by="ultra",
    missing_hard=True,
)
DISAGREED = _pair(
    "uncertain",
    [_op("nano", "regressed"), _op("super", "regressed"), _op("ultra", "equivalent")],
    decided_by="ultra",
)
UNCERTAIN = _pair("uncertain", [_op("ultra", "uncertain")], decided_by="ultra")
TIMEOUT = _pair("uncertain", [_op("ultra", "uncertain", 0.0)], error_code="judge_timeout")
HARD = _pair("regression", decided_by="level0", hard_fail=True)
DRIFT = _pair("drift", decided_by="drift")
IMPROVED = _pair("improved", [_op("super", "improved")], decided_by="super")
FAILED = _pair("failed", decided_by="infra", error_code="harness_failed")


@dataclass(frozen=True)
class Row:
    name: str
    pairs: list[PairVerdict]
    decision: str
    action: str
    reason: str
    n_failed: int = 0
    n_total: int | None = None
    ultra_timed_out: bool = False


ROWS = [
    Row("level0 hard fail", [PASS, HARD], "BLOCK", "reverted", "level0_hard_fail"),
    Row("super and ultra agree", [PASS, AGREED], "BLOCK", "reverted", "agreed_regression"),
    Row("missing hard + ultra", [MISSING_HARD], "BLOCK", "reverted", "agreed_regression"),
    Row("single judge regression", [PASS, ULTRA_ONLY], "WARN", "held", "single_judge_regression"),
    Row("judges disagreed", [PASS, DISAGREED], "WARN", "held", "judges_disagreed"),
    Row("uncertain pair", [PASS, UNCERTAIN], "WARN", "held", "uncertain"),
    Row("ultra timeout flag", [PASS], "WARN", "held", "judge_timeout", ultra_timed_out=True),
    Row("ultra timeout pair", [PASS, TIMEOUT], "WARN", "held", "judge_timeout"),
    Row("all pass", [PASS, PASS], "PASS", "merged", "all_clear"),
    Row("pass improved drift", [PASS, IMPROVED, DRIFT], "PASS", "merged", "drift"),
    Row("replay infra", [PASS, PASS, PASS], "WARN", "held", "replay_infra", 2, 5),
    Row("few failures", [PASS] * 9 + [FAILED], "WARN", "held", "replay_failed", 1, 10),
    Row("no traces", [], "PASS", "merged", "no_affected_traces", 0, 0),
    Row("block beats infra", [HARD], "BLOCK", "reverted", "level0_hard_fail", 4, 5),
]


@pytest.mark.parametrize("row", ROWS, ids=[r.name for r in ROWS])
def test_agreement_table_row(row: Row) -> None:
    n_total = row.n_total if row.n_total is not None else len(row.pairs)
    result = d.decide(
        row.pairs, n_failed=row.n_failed, n_total=n_total, ultra_timed_out=row.ultra_timed_out
    )
    assert (result.decision, result.action) == (row.decision, row.action)
    assert row.reason in result.reasons


def test_every_rule_is_exercised_by_the_table() -> None:
    exercised = set()
    for row in ROWS:
        n_total = row.n_total if row.n_total is not None else len(row.pairs)
        result = d.decide(
            row.pairs, n_failed=row.n_failed, n_total=n_total, ultra_timed_out=row.ultra_timed_out
        )
        exercised.update(result.reasons)
    assert {r.reason for r in d.RULES} <= exercised


def test_level0_off_hard_fail_on_a_cascade_pair_does_not_block() -> None:
    cascaded = _pair("regression", [_op("nano", "regressed")], hard_fail=True)
    assert d.decide([cascaded], n_failed=0, n_total=1, ultra_timed_out=False).decision == "WARN"


def test_single_tier_modes_may_block_on_their_only_judge() -> None:
    nano_only = _pair("regression", [_op("nano", "regressed")])
    plain = d.decide([nano_only], n_failed=0, n_total=1, ultra_timed_out=False)
    single = d.decide(
        [nano_only], n_failed=0, n_total=1, ultra_timed_out=False, single_judge_blocks=True
    )
    assert plain.decision == "WARN" and single.decision == "BLOCK"


def test_unmatched_state_is_warn_never_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "RULES", ())
    assert d.decide([PASS], n_failed=0, n_total=1, ultra_timed_out=False).decision == "WARN"


def test_merge_conflict_holds() -> None:
    result = d.merge_conflict(d.DecisionResult("PASS", "merged", ["all_clear"]))
    assert (result.decision, result.action, result.reasons) == ("WARN", "held", ["merge_conflict"])
