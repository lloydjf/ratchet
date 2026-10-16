"""Bench metrics as pure functions over :class:`~ratchet.bench.results.EditResult` lists.

Definitions:

- detection recall: R1–R6 edits with decision ∈ {BLOCK, WARN}; block recall: decision = BLOCK
- false-block rate: N0 and D1 edits with decision = BLOCK; false-warn rate: N0 with WARN
- drift accuracy: D1 edits whose every affected pair (expected ``drift``) came out ``drift``
- pair precision/recall over ``expected_pair_outcomes``: positive = expected ``regression``,
  predicted positive = outcome ``regression`` or ``uncertain`` (both stop a merge)
- cost per verdict: judge USD by tier (opinion tokens × list prices), mean and p95, plus the
  all-Ultra counterfactual; latency: edit → verdict wall time, mean and p95
- judge agreement: Cohen's κ between each tier's opinions and reference pair labels, binary
  (``regressed``/``uncertain`` = flag; ``equivalent``/``improved`` = ok)
- E6 noise floor: share of unchanged-skill pairs judged ``pass`` (or ``improved``)

Edits that produced no verdict (``error``) are excluded from every rate and counted apart.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from ratchet.bench.results import EditResult
from ratchet.bench.taxonomy import REGRESSION_TYPES
from ratchet.models import TIERS, Tier

__all__ = [
    "Rate",
    "Stats",
    "Kappa",
    "NoiseFloor",
    "RunMetrics",
    "rate",
    "percentile",
    "stats",
    "cohen_kappa",
    "detection_recall",
    "block_recall",
    "recall_by_type",
    "false_block_rate",
    "false_warn_rate",
    "drift_accuracy",
    "pair_precision_recall",
    "cost_stats",
    "latency_stats",
    "kappa_by_tier",
    "construction_reference",
    "noise_floor",
    "summarize",
    "opinion_flag",
    "outcome_flag",
    "FLAGGED_OUTCOMES",
]

FLAGGED_OUTCOMES = frozenset({"regression", "uncertain"})
_DETECTED = frozenset({"BLOCK", "WARN"})
_CLEAR_OPINIONS = frozenset({"equivalent", "improved"})


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Rate(_M):
    num: int
    den: int

    @property
    def value(self) -> float | None:
        return self.num / self.den if self.den else None


class Stats(_M):
    n: int
    mean: float | None
    p95: float | None


class Kappa(_M):
    n: int
    value: float | None


class NoiseFloor(_M):
    n_pairs: int
    pass_rate: float | None
    by_outcome: dict[str, int] = Field(default_factory=dict)
    noisy_rate: float | None = None  # ADR 0023: share of pairs whose control replay diverged


class RunMetrics(_M):
    n_edits: int
    n_errors: int
    recall: Rate
    block_recall: Rate
    recall_by_type: dict[str, Rate]
    block_recall_by_type: dict[str, Rate]
    false_block: Rate
    false_block_by_type: dict[str, Rate]
    false_warn: Rate
    drift_accuracy: Rate
    pair_precision: Rate
    pair_recall: Rate
    cost_by_tier: dict[Tier, Stats]
    cost_total: Stats
    cost_counterfactual: Stats
    latency: Stats
    kappa_construction: dict[Tier, Kappa]
    kappa_human: dict[Tier, Kappa]
    noise: NoiseFloor | None = None


def rate(items: Iterable[EditResult], hit: Callable[[EditResult], bool]) -> Rate:
    pool = [e for e in items if e.error is None]
    return Rate(num=sum(1 for e in pool if hit(e)), den=len(pool))


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear-interpolated percentile (``q`` in [0, 100]); ``None`` for no values."""
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * (q / 100.0)
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def stats(values: Sequence[float]) -> Stats:
    if not values:
        return Stats(n=0, mean=None, p95=None)
    return Stats(n=len(values), mean=sum(values) / len(values), p95=percentile(values, 95))


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Cohen's κ = (p_o − p_e) / (1 − p_e) for two raters over the same items.

    ``None`` when there are no items or chance agreement is total (p_e = 1, κ undefined).
    """
    if len(a) != len(b):
        raise ValueError("raters must label the same items")
    n = len(a)
    if n == 0:
        return None
    p_o = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    p_e = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    if math.isclose(p_e, 1.0):
        return None
    return (p_o - p_e) / (1 - p_e)


# --- edit-level rates -------------------------------------------------------------------------
def _of(edits: Iterable[EditResult], types: Iterable[str]) -> list[EditResult]:
    wanted = set(types)
    return [e for e in edits if e.type in wanted]


def detection_recall(edits: Iterable[EditResult]) -> Rate:
    return rate(_of(edits, REGRESSION_TYPES), lambda e: e.decision in _DETECTED)


def block_recall(edits: Iterable[EditResult]) -> Rate:
    return rate(_of(edits, REGRESSION_TYPES), lambda e: e.decision == "BLOCK")


def recall_by_type(edits: Sequence[EditResult], *, block: bool = False) -> dict[str, Rate]:
    fn = block_recall if block else detection_recall
    return {t: fn(_of(edits, [t])) for t in REGRESSION_TYPES}


def false_block_rate(edits: Iterable[EditResult], types: Iterable[str] = ("N0", "D1")) -> Rate:
    return rate(_of(edits, types), lambda e: e.decision == "BLOCK")


def false_warn_rate(edits: Iterable[EditResult]) -> Rate:
    return rate(_of(edits, ["N0"]), lambda e: e.decision == "WARN")


def _drift_ok(e: EditResult) -> bool:
    """The world change was recognised wherever it showed: every labeled pair that differed
    is ``drift`` (none ``regression``/``uncertain``), at least one is, and the edit passed.
    A labeled pair whose replay happened not to differ this time (``pass``) is not a miss."""
    affected = [p for p in e.pairs if p.expected_outcome == "drift"]
    shown = [p for p in affected if p.outcome != "pass"]
    return (
        bool(affected)
        and bool(shown)
        and all(p.outcome == "drift" for p in shown)
        and e.decision == "PASS"
    )


def drift_accuracy(edits: Iterable[EditResult]) -> Rate:
    """D1 edits with labeled affected pairs, scored by :func:`_drift_ok`."""
    pool = [e for e in _of(edits, ["D1"]) if any(p.expected_outcome == "drift" for p in e.pairs)]
    return rate(pool, _drift_ok)


def pair_precision_recall(edits: Iterable[EditResult]) -> tuple[Rate, Rate]:
    tp = fp = fn = 0
    for e in edits:
        if e.error is not None:
            continue
        for p in e.pairs:
            if p.outcome == "failed":
                continue
            actual = p.expected_outcome == "regression"
            predicted = p.outcome in FLAGGED_OUTCOMES
            tp += actual and predicted
            fp += predicted and not actual
            fn += actual and not predicted
    return Rate(num=tp, den=tp + fp), Rate(num=tp, den=tp + fn)


# --- cost and latency -------------------------------------------------------------------------
def cost_stats(edits: Iterable[EditResult]) -> tuple[dict[Tier, Stats], Stats, Stats]:
    pool = [e for e in edits if e.error is None]
    by_tier = {t: stats([e.judge_usd_by_tier.get(t, 0.0) for e in pool]) for t in TIERS}
    total = stats([e.judge_usd for e in pool])
    counterfactual = stats([e.counterfactual_ultra_usd for e in pool])
    return by_tier, total, counterfactual


def latency_stats(edits: Iterable[EditResult]) -> Stats:
    return stats([e.latency_s for e in edits if e.error is None])


# --- judge agreement --------------------------------------------------------------------------
def opinion_flag(verdict: str) -> str:
    return "ok" if verdict in _CLEAR_OPINIONS else "flag"


def outcome_flag(label: str) -> str:
    return "flag" if label == "regression" else "ok"


def kappa_by_tier(
    edits: Iterable[EditResult], reference: Mapping[tuple[str, str], str]
) -> dict[Tier, Kappa]:
    """κ per tier over pairs that tier judged and that have a reference label.

    ``reference`` maps ``(bench_id, task_id)`` to a pair label (``regression``/``pass``/
    ``drift``); the tier's last opinion on the pair is its rating.
    """
    ratings: dict[Tier, tuple[list[str], list[str]]] = {t: ([], []) for t in TIERS}
    for e in edits:
        if e.error is not None:
            continue
        for p in e.pairs:
            ref = reference.get((e.bench_id, p.task_id))
            if ref is None:
                continue
            last: dict[Tier, str] = {op.tier: op.verdict for op in p.opinions}
            for tier, verdict in last.items():
                ratings[tier][0].append(opinion_flag(verdict))
                ratings[tier][1].append(outcome_flag(ref))
    return {t: Kappa(n=len(a), value=cohen_kappa(a, b)) for t, (a, b) in ratings.items()}


def construction_reference(edits: Iterable[EditResult]) -> dict[tuple[str, str], str]:
    return {(e.bench_id, p.task_id): p.expected_outcome for e in edits for p in e.pairs}


# --- E6 ---------------------------------------------------------------------------------------
def noise_floor(edits: Iterable[EditResult]) -> NoiseFloor:
    counts: Counter[str] = Counter()
    noisy = 0
    for e in edits:
        if e.error is None:
            kept = [p for p in e.pairs if p.outcome != "failed"]
            counts.update(p.outcome for p in kept)
            noisy += sum(p.control_outcome == "diverges" for p in kept)
    n = sum(counts.values())
    passed = counts["pass"] + counts["improved"]
    return NoiseFloor(
        n_pairs=n,
        pass_rate=passed / n if n else None,
        by_outcome=dict(counts),
        noisy_rate=noisy / n if n else None,
    )


def summarize(
    edits: Sequence[EditResult],
    *,
    human: Mapping[tuple[str, str], str] | None = None,
    noise: Sequence[EditResult] | None = None,
) -> RunMetrics:
    precision, recall = pair_precision_recall(edits)
    by_tier, total, counterfactual = cost_stats(edits)
    return RunMetrics(
        n_edits=len(edits),
        n_errors=sum(1 for e in edits if e.error is not None),
        recall=detection_recall(edits),
        block_recall=block_recall(edits),
        recall_by_type=recall_by_type(edits),
        block_recall_by_type=recall_by_type(edits, block=True),
        false_block=false_block_rate(edits),
        false_block_by_type={t: false_block_rate(edits, [t]) for t in ("N0", "D1")},
        false_warn=false_warn_rate(edits),
        drift_accuracy=drift_accuracy(edits),
        pair_precision=precision,
        pair_recall=recall,
        cost_by_tier=by_tier,
        cost_total=total,
        cost_counterfactual=counterfactual,
        latency=latency_stats(edits),
        kappa_construction=kappa_by_tier(edits, construction_reference(edits)),
        kappa_human=kappa_by_tier(edits, human or {}),
        noise=noise_floor(noise) if noise else None,
    )
