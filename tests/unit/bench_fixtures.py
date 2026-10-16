"""Tiny hand-made bench results for the metrics, report and compare tests."""

from __future__ import annotations

from datetime import UTC, datetime

from ratchet.bench.dataset import BenchConfig, CascadeSpec
from ratchet.bench.results import (
    EditResult,
    OpinionResult,
    PairResult,
    ResultsFile,
    ResultsMeta,
    RunResult,
)
from ratchet.models import Decision, PairOutcome

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def op(tier: str, verdict: str, conf: float = 0.9, pt: int = 1000, ct: int = 100) -> OpinionResult:
    return OpinionResult.model_validate(
        {
            "tier": tier,
            "verdict": verdict,
            "confidence": conf,
            "prompt_tokens": pt,
            "completion_tokens": ct,
        }
    )


def pair(
    task: str, outcome: PairOutcome, expected: PairOutcome = "pass", *ops: OpinionResult
) -> PairResult:
    return PairResult(
        golden_run_id=f"run_{task}",
        replay_run_id=f"rep_{task}",
        task_id=task,
        outcome=outcome,
        expected_outcome=expected,
        decided_by=ops[-1].tier if ops else "level0",
        opinions=list(ops),
    )


def edit(
    bench_id: str,
    type_: str,
    decision: Decision | None,
    pairs: list[PairResult],
    *,
    usd: float = 0.001,
    latency: float = 10.0,
    error: str | None = None,
) -> EditResult:
    expected: Decision = "PASS" if type_ in ("N0", "D1") else "BLOCK"
    return EditResult(
        bench_id=bench_id,
        skill="expense-report",
        type=type_,
        expected_decision=expected,
        decision=decision,
        pairs=pairs,
        judge_usd_by_tier={"nano": usd / 4, "super": usd / 4, "ultra": usd / 2},
        counterfactual_ultra_usd=usd * 3,
        latency_s=latency,
        error=error,
        prompt_tokens_by_tier={"nano": 2000, "super": 4000, "ultra": 1000},
        cached_prompt_tokens_by_tier={"nano": 0, "super": 2000, "ultra": 250},
        narrative=f"Narrative for {bench_id}.",
        reasons=["agreed_regression"] if decision == "BLOCK" else ["all_clear"],
    )


def small_edits() -> list[EditResult]:
    nano_eq, nano_reg = op("nano", "equivalent", 0.95), op("nano", "regressed", 0.93)
    sup_reg, ult_reg = op("super", "regressed"), op("ultra", "regressed")
    return [
        edit(
            "r2-a",
            "R2",
            "BLOCK",
            [
                pair("t1", "regression", "regression", nano_reg, sup_reg, ult_reg),
                pair("t2", "pass", "pass", nano_eq),
            ],
        ),
        edit("r4-a", "R4", "BLOCK", [pair("t3", "regression", "regression")]),
        edit("r5-a", "R5", "PASS", [pair("t4", "pass", "regression", nano_eq)], usd=0.004),
        edit(
            "r1-a", "R1", "WARN", [pair("t5", "uncertain", "regression", op("nano", "uncertain"))]
        ),
        edit("n0-a", "N0", "PASS", [pair("t1", "pass", "pass", nano_eq)], usd=0.0005),
        edit("n0-b", "N0", "WARN", [pair("t2", "uncertain", "pass", op("nano", "uncertain"))]),
        edit("d1-a", "D1", "PASS", [pair("t6", "drift", "drift", nano_reg)]),
        edit(
            "d1-b", "D1", "BLOCK", [pair("t7", "regression", "drift", nano_reg, sup_reg, ult_reg)]
        ),
        edit("r3-x", "R3", None, [], error="no_verdict:failed_infra"),
    ]


def run(name: str, judge: str, edits: list[EditResult], **cfg: object) -> RunResult:
    config = BenchConfig.model_validate(
        {"name": name, "cascade": CascadeSpec(mode=judge).model_dump(), **cfg}  # type: ignore[arg-type]
    )
    return RunResult(
        config=config,
        config_path=f"bench/configs/{name}.yaml",
        command=f"ratchet bench run --config bench/configs/{name}.yaml --out bench/results/x",
        started_at=T0,
        updated_at=T0,
        edits=edits,
    )


def small_results(*, simulated: bool = True) -> ResultsFile:
    noise = [
        edit("noise-expense-report-1", "N0", "PASS", [pair("t1", "pass"), pair("t2", "pass")]),
        edit("noise-expense-report-2", "N0", "WARN", [pair("t1", "pass"), pair("t2", "uncertain")]),
    ]
    meta = ResultsMeta(
        created_at=T0,
        updated_at=T0,
        git_sha="abc1234",
        harness="minimal",
        simulated=simulated,
        models={
            "nano": "ratchet-sim/nano",
            "super": "ratchet-sim/super",
            "ultra": "ratchet-sim/ultra",
        },
        agent_tier="super",
        labels_path="bench/results/x/labels.json",
        n_labels=9,
    )
    cheap = [e.model_copy(update={"judge_usd_by_tier": {"nano": 0.0002}}) for e in small_edits()]
    return ResultsFile(
        meta=meta,
        runs={
            "cascade": run("cascade", "cascade", small_edits(), experiments=["E1", "E4"]),
            "nano_only": run("nano_only", "nano_only", cheap, experiments=["E4"]),
            "noise": run("noise", "cascade", noise, mode="noise", experiments=["E6"]),
        },
    )
