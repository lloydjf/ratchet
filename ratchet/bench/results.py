"""``results.json``: every verdict of every configuration run into one results directory.

One file holds all runs (``runs[<config name>]``), so the report can put the four E4
configurations, the E3/E5 ablations and the E6 noise floor side by side. A run is resumable:
an edit already in ``runs[<config>].edits`` is skipped on the next ``ratchet bench run``.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ratchet import clock
from ratchet.bench.dataset import BenchConfig
from ratchet.bench.labels import EditLabel
from ratchet.config.models import model_for
from ratchet.config.pricing import cost_usd, counterfactual_ultra_usd
from ratchet.config.settings import Settings
from ratchet.judge.decide import decide
from ratchet.models import TIERS, Decision, PairOutcome, PairVerdict, Tier, Verdict
from ratchet.store.base import write_json_atomic
from ratchet.store.ledger import TierTotals

__all__ = [
    "OpinionResult",
    "DriftResult",
    "PairResult",
    "EditResult",
    "RunResult",
    "ResultsMeta",
    "ResultsFile",
    "load_results",
    "save_results",
    "pair_result",
    "edit_result",
    "with_prompt_tokens",
    "bench_decision",
    "make_meta",
    "git_sha",
    "SIMULATED_LABEL",
]

SIMULATED_LABEL = "SIMULATED MODELS — pipeline smoke test; not benchmark results"


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpinionResult(_M):
    tier: Tier
    verdict: str
    confidence: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usd: float = 0.0


class DriftResult(_M):
    fact_id: str
    changed: bool
    status: str
    attributable: bool | None = None
    old_value: str = ""
    new_value: str | None = None


class PairResult(_M):
    golden_run_id: str
    replay_run_id: str
    task_id: str
    outcome: PairOutcome
    expected_outcome: PairOutcome = "pass"
    decided_by: str = ""
    reason: str = ""
    opinions: list[OpinionResult] = Field(default_factory=list)
    control_outcome: str | None = None  # ADR 0023: "agrees" | "diverges" when a control ran
    drift: list[DriftResult] = Field(default_factory=list)
    level0_hard_fail: bool = False
    level0_new: int = 0
    level0_missing: int = 0
    level0_changed: int = 0


class EditResult(_M):
    bench_id: str
    skill: str
    type: str
    expected_decision: Decision
    edit_id: str = ""
    verdict_id: str = ""
    state: str = ""
    decision: Decision | None = None  # scored decision (single-judge rule applied for E4)
    pipeline_decision: Decision | None = None
    action: str = ""
    reasons: list[str] = Field(default_factory=list)
    headline: str = ""
    narrative: str = ""
    pairs: list[PairResult] = Field(default_factory=list)
    judge_usd_by_tier: dict[Tier, float] = Field(default_factory=dict)  # opinion tokens x prices
    counterfactual_ultra_usd: float = 0.0
    spent_usd_by_tier: dict[Tier, float] = Field(default_factory=dict)  # proxy ledger, all calls
    prompt_tokens_by_tier: dict[Tier, int] = Field(default_factory=dict)  # proxy ledger
    cached_prompt_tokens_by_tier: dict[Tier, int] = Field(default_factory=dict)  # prompt-cache hits
    latency_s: float = 0.0
    simulated_models: bool = False
    repeat: int = 0  # E6 noise runs: 1..k
    error: str | None = None

    @property
    def judge_usd(self) -> float:
        return sum(self.judge_usd_by_tier.values())


class RunResult(_M):
    config: BenchConfig
    config_path: str = ""
    command: str = ""
    started_at: datetime
    updated_at: datetime
    edits: list[EditResult] = Field(default_factory=list)

    def done_ids(self) -> set[str]:
        return {e.bench_id for e in self.edits if e.error is None}


class ResultsMeta(_M):
    created_at: datetime
    updated_at: datetime
    git_sha: str
    harness: str
    simulated: bool
    models: dict[Tier, str]
    agent_tier: Tier
    labels_path: str = ""
    n_labels: int = 0


class ResultsFile(_M):
    meta: ResultsMeta
    runs: dict[str, RunResult] = Field(default_factory=dict)


def load_results(path: Path) -> ResultsFile | None:
    if not path.is_file():
        return None
    return ResultsFile.model_validate_json(path.read_text(encoding="utf-8"))


def save_results(path: Path, results: ResultsFile) -> None:
    write_json_atomic(path, results)


def git_sha(cwd: Path | None = None) -> str:
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except OSError:
        return "unknown"
    return out.stdout.strip() or "unknown"


def make_meta(settings: Settings, *, labels_path: str, n_labels: int) -> ResultsMeta:
    now = clock.now()
    return ResultsMeta(
        created_at=now,
        updated_at=now,
        git_sha=git_sha(),
        harness=settings.ratchet_harness,
        simulated=settings.simulated,
        models={t: model_for(t, settings) for t in TIERS},
        agent_tier=settings.ratchet_agent_tier,
        labels_path=labels_path,
        n_labels=n_labels,
    )


def pair_result(pair: PairVerdict, task_id: str, expected: PairOutcome) -> PairResult:
    f = pair.level0
    return PairResult(
        golden_run_id=pair.golden_run_id,
        replay_run_id=pair.replay_run_id,
        task_id=task_id,
        outcome=pair.outcome,
        control_outcome=pair.control_outcome,
        expected_outcome=expected,
        decided_by=pair.decided_by,
        reason=pair.reason,
        opinions=[
            OpinionResult(
                tier=o.tier,
                verdict=o.verdict,
                confidence=o.confidence,
                prompt_tokens=o.prompt_tokens,
                completion_tokens=o.completion_tokens,
                usd=o.usd,
            )
            for o in pair.opinions
        ],
        drift=[
            DriftResult(
                fact_id=d.fact_id,
                changed=d.changed,
                status=d.status,
                attributable=d.attributable,
                old_value=d.old_value,
                new_value=d.new_value,
            )
            for d in pair.drift
        ],
        level0_hard_fail=f.hard_fail,
        level0_new=len(f.new_calls),
        level0_missing=len(f.missing_calls),
        level0_changed=len(f.changed_args),
    )


def bench_decision(verdict: Verdict, *, single_judge_blocks: bool) -> Decision:
    """The scored decision. In the single-tier E4 modes the only judge may BLOCK (decide.py)."""
    if not single_judge_blocks or not verdict.pairs:
        return verdict.decision
    n_failed = sum(p.outcome == "failed" for p in verdict.pairs)
    timed_out = any(p.error_code == "judge_timeout" for p in verdict.pairs)
    result = decide(
        verdict.pairs,
        n_failed=n_failed,
        n_total=len(verdict.pairs),
        ultra_timed_out=timed_out,
        single_judge_blocks=True,
    )
    return result.decision


def _token_costs(pairs: list[PairVerdict]) -> tuple[dict[Tier, float], float]:
    """Judge cost from opinion tokens at list prices (independent of response caching)."""
    by_tier: dict[Tier, float] = dict.fromkeys(TIERS, 0.0)
    prompt = completion = 0
    for pair in pairs:
        for op in pair.opinions:
            by_tier[op.tier] += cost_usd(op.tier, op.prompt_tokens, op.completion_tokens)
            prompt += op.prompt_tokens
            completion += op.completion_tokens
    rounded: dict[Tier, float] = {t: round(v, 8) for t, v in by_tier.items()}
    return rounded, round(counterfactual_ultra_usd(prompt, completion), 8)


def edit_result(
    label: EditLabel,
    verdict: Verdict | None,
    *,
    edit_id: str,
    state: str,
    latency_s: float,
    task_for_run: dict[str, str],
    single_judge_blocks: bool,
    repeat: int = 0,
) -> EditResult:
    base = EditResult(
        bench_id=label.edit_id,
        skill=label.skill,
        type=label.type,
        expected_decision=label.effective_decision(),
        edit_id=edit_id,
        state=state,
        latency_s=latency_s,
        repeat=repeat,
    )
    if verdict is None:
        return base.model_copy(update={"error": f"no_verdict:{state}"})
    pairs = []
    for p in verdict.pairs:
        task_id = task_for_run.get(p.golden_run_id, p.golden_run_id)
        pairs.append(pair_result(p, task_id, label.expected_outcome(task_id)))
    by_tier, counterfactual = _token_costs(verdict.pairs)
    return base.model_copy(
        update={
            "verdict_id": verdict.verdict_id,
            "decision": bench_decision(verdict, single_judge_blocks=single_judge_blocks),
            "pipeline_decision": verdict.decision,
            "action": verdict.action,
            "reasons": list(verdict.reasons),
            "headline": verdict.headline,
            "narrative": verdict.narrative,
            "pairs": pairs,
            "judge_usd_by_tier": by_tier,
            "counterfactual_ultra_usd": counterfactual,
            "spent_usd_by_tier": {t: round(v, 8) for t, v in verdict.cost.items()},
            "simulated_models": verdict.simulated_models,
        }
    )


def with_prompt_tokens(result: EditResult, totals: TierTotals) -> EditResult:
    """The edit's prompt tokens and prompt-cache hits by tier, from its ledger rows."""
    return result.model_copy(
        update={
            "prompt_tokens_by_tier": dict(totals.prompt_tokens),
            "cached_prompt_tokens_by_tier": dict(totals.cached_prompt_tokens),
        }
    )
