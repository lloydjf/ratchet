"""Edit-level decision from pair verdicts: the agreement table, as data.

``RULES`` is evaluated top to bottom; the first row whose condition holds sets the decision
and action, and every later row with the same decision adds its reason code. Order:

1. a Level 0 hard fail on any pair still ``regression`` → BLOCK / reverted
   (``level0_hard_fail``; a hard fail a diverging control turned into ``pass``, ADR 0023
   second amendment, keeps its finding as evidence but no longer counts)
2. a ``regression`` pair where Super and Ultra both said ``regressed``, or a Level 0
   ``missing_hard`` call plus Ultra ``regressed`` → BLOCK / reverted (``agreed_regression``)
3. more than 20 % of pairs failed to replay → WARN / held (``replay_infra``)
4. the final judge timed out → WARN / held (``judge_timeout``)
5. Super and Ultra disagreed on a pair → WARN / held (``judges_disagreed``)
6. a ``regression`` on a single judge → WARN / held (``single_judge_regression``)
7. an ``uncertain`` pair → WARN / held (``uncertain``); a ``failed`` pair → (``replay_failed``)
8. nothing to replay → PASS / merged (``no_affected_traces``)
9. every pair ``pass``, ``improved`` or ``drift`` → PASS / merged (``drift`` noted if any)

Anything that matches no row is WARN / held (``uncertain``): unknown never passes. A git
``merge_conflict`` happens after the decision; the caller replaces the result with
:func:`merge_conflict`. ``single_judge_blocks=True`` is for the bench's single-tier E4
configurations, where the only judge's ``regressed`` must be allowed to BLOCK.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ratchet.models import Action, Decision, JudgeOpinion, PairVerdict, Tier

__all__ = ["DecisionResult", "Facts", "Rule", "RULES", "decide", "merge_conflict", "facts"]

INFRA_RATIO = 0.20


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    action: Action
    reasons: list[str]


@dataclass(frozen=True)
class Facts:
    n_pairs: int
    n_total: int
    infra_ratio: float
    hard_fail: bool
    agreed_regression: bool
    disagreed: bool
    single_regression: bool
    uncertain: bool
    failed: bool
    ultra_timed_out: bool
    all_clear: bool
    drift: bool


@dataclass(frozen=True)
class Rule:
    reason: str
    when: Callable[[Facts], bool]
    decision: Decision
    action: Action


RULES: tuple[Rule, ...] = (
    Rule("level0_hard_fail", lambda f: f.hard_fail, "BLOCK", "reverted"),
    Rule("agreed_regression", lambda f: f.agreed_regression, "BLOCK", "reverted"),
    Rule("replay_infra", lambda f: f.infra_ratio > INFRA_RATIO, "WARN", "held"),
    Rule("judge_timeout", lambda f: f.ultra_timed_out, "WARN", "held"),
    Rule("judges_disagreed", lambda f: f.disagreed, "WARN", "held"),
    Rule("single_judge_regression", lambda f: f.single_regression, "WARN", "held"),
    Rule("uncertain", lambda f: f.uncertain, "WARN", "held"),
    Rule("replay_failed", lambda f: f.failed, "WARN", "held"),
    Rule("no_affected_traces", lambda f: f.n_pairs == 0 and f.n_total == 0, "PASS", "merged"),
    Rule("all_clear", lambda f: f.all_clear and f.n_pairs > 0, "PASS", "merged"),
    Rule("drift", lambda f: f.all_clear and f.drift, "PASS", "merged"),
)
_FALLBACK = DecisionResult("WARN", "held", ["uncertain"])
_CLEAR = frozenset({"pass", "improved", "drift"})


def _last(pair: PairVerdict, tier: Tier) -> JudgeOpinion | None:
    found = [o for o in pair.opinions if o.tier == tier]
    return found[-1] if found else None


def _agreed(pair: PairVerdict, single_judge_blocks: bool) -> bool:
    if pair.outcome != "regression" or pair.decided_by == "level0":
        return False
    sup, ultra = _last(pair, "super"), _last(pair, "ultra")
    ultra_regressed = ultra is not None and ultra.verdict == "regressed"
    if ultra_regressed and sup is not None and sup.verdict == "regressed":
        return True
    if ultra_regressed and pair.level0.missing_hard:
        return True
    return single_judge_blocks and len(pair.opinions) == 1


def _disagreed(pair: PairVerdict) -> bool:
    sup, ultra = _last(pair, "super"), _last(pair, "ultra")
    return (
        pair.outcome == "uncertain"
        and sup is not None
        and ultra is not None
        and sup.verdict == "regressed"
        and ultra.verdict in ("equivalent", "improved")
    )


def facts(
    pairs: list[PairVerdict],
    *,
    n_failed: int,
    n_total: int,
    ultra_timed_out: bool,
    single_judge_blocks: bool = False,
) -> Facts:
    agreed = [p for p in pairs if _agreed(p, single_judge_blocks)]
    return Facts(
        n_pairs=len(pairs),
        n_total=n_total,
        infra_ratio=(n_failed / n_total) if n_total > 0 else 0.0,
        hard_fail=any(
            p.outcome == "regression" and p.decided_by == "level0" and p.level0.hard_fail
            for p in pairs
        ),
        agreed_regression=bool(agreed),
        disagreed=any(_disagreed(p) for p in pairs),
        single_regression=any(p.outcome == "regression" and p not in agreed for p in pairs),
        uncertain=any(p.outcome == "uncertain" for p in pairs),
        failed=any(p.outcome == "failed" for p in pairs) or n_failed > 0,
        ultra_timed_out=ultra_timed_out or any(p.error_code == "judge_timeout" for p in pairs),
        all_clear=all(p.outcome in _CLEAR for p in pairs) and n_failed == 0,
        drift=any(p.outcome == "drift" for p in pairs),
    )


def decide(
    pairs: list[PairVerdict],
    *,
    n_failed: int,
    n_total: int,
    ultra_timed_out: bool,
    single_judge_blocks: bool = False,
) -> DecisionResult:
    f = facts(
        pairs,
        n_failed=n_failed,
        n_total=n_total,
        ultra_timed_out=ultra_timed_out,
        single_judge_blocks=single_judge_blocks,
    )
    matched = [rule for rule in RULES if rule.when(f)]
    if not matched:
        return _FALLBACK
    first = matched[0]
    reasons = [r.reason for r in matched if r.decision == first.decision]
    if first.decision == "PASS" and "no_affected_traces" in reasons:
        reasons = ["no_affected_traces"]
    return DecisionResult(first.decision, first.action, reasons)


def merge_conflict(_: DecisionResult | None = None) -> DecisionResult:
    """The merge failed after a PASS → WARN ``merge_conflict``, hold."""
    return DecisionResult("WARN", "held", ["merge_conflict"])
