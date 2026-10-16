"""The bench pipeline in process on the offline simulator: goldens → N0/D1 controls → a cascade
run through the real orchestrator → a judge-only ablation that reuses those replays →
resume → report. Simulated models only (ADR 0018); no network."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from ratchet.bench.build import BuildPlan, build_dataset
from ratchet.bench.dataset import BenchPaths, load_tasks
from ratchet.bench.env import BenchEnv
from ratchet.bench.labels import load_labels
from ratchet.bench.publish import write_report
from ratchet.bench.results import load_results
from ratchet.bench.runner import RunOptions, run_config
from ratchet.bench.smoke import smoke_settings
from ratchet.config.settings import Settings, configure, reset_settings

BENCH = BenchPaths(Path(__file__).resolve().parents[2] / "bench")
TASKS = {"fx-berlin-trip", "fx-london", "canary-percent", "canary-summary", "canary-formal-rewrite"}


@pytest.fixture
def env(tmp_path: Path) -> Iterator[BenchEnv]:
    base = smoke_settings(Settings(_env_file=None, ratchet_log_level="ERROR"))  # type: ignore[call-arg]
    bench = BenchEnv.open(
        BENCH,
        tmp_path / "work",
        base,
        edits_root=tmp_path / "edits",
        labels_path=tmp_path / "labels.json",
        tavily_dir=tmp_path / "tavily",
    )
    configure(bench.settings)
    try:
        yield bench
    finally:
        reset_settings()


def test_controls_run_through_sequence_b_and_ablations_reuse_replays(
    env: BenchEnv, tmp_path: Path
) -> None:
    assert env.settings.simulated
    tasks = [t for t in load_tasks(BENCH.tasks) if t.task_id in TASKS]
    plan = BuildPlan(
        tasks=tasks,
        skills=["fx-expense-converter"],
        inject=False,
        n0_skills=["fx-expense-converter"],
        d1_ids=["d1-fx-expense-converter-1"],
    )
    asyncio.run(build_dataset(env, plan, echo=lambda _: None))
    labels = load_labels(env.labels_path).by_id()
    assert set(labels) == {
        "n0-fx-expense-converter-1",
        "n0-fx-expense-converter-2",
        "d1-fx-expense-converter-1",
    }
    d1 = labels["d1-fx-expense-converter-1"]
    assert d1.affected_goldens == ["fx-berlin-trip"] and d1.manifested
    assert all(lab.review.required for lab in labels.values())  # controls: 100% review

    out = tmp_path / "results"
    cascade = RunOptions(config_path=BENCH.configs / "cascade.yaml", out=out)
    asyncio.run(run_config(env, cascade, echo=lambda _: None, confirm=lambda _: False))
    results = load_results(out / "results.json")
    assert results is not None and results.meta.simulated
    by_id = {e.bench_id: e for e in results.runs["cascade"].edits}
    assert {e.decision for e in by_id.values()} == {"PASS"}
    drift_pairs = {p.task_id: p.outcome for p in by_id["d1-fx-expense-converter-1"].pairs}
    assert drift_pairs["fx-berlin-trip"] == "drift" and drift_pairs["fx-london"] == "pass"

    # Resume: nothing left to run for the same config.
    asyncio.run(run_config(env, cascade, echo=lambda _: None, confirm=lambda _: False))
    assert len(load_results(out / "results.json").runs["cascade"].edits) == 3  # type: ignore[union-attr]

    nano = RunOptions(config_path=BENCH.configs / "nano_only.yaml", out=out)
    agent_spend_before = env.store.spend(purposes=["agent"]).total_usd
    asyncio.run(run_config(env, nano, echo=lambda _: None, confirm=lambda _: False))
    assert env.store.spend(purposes=["agent"]).total_usd == agent_spend_before  # replays reused
    final = load_results(out / "results.json")
    assert final is not None and len(final.runs["nano_only"].edits) == 3
    assert all(
        p.opinions[0].tier == "nano"
        for e in final.runs["nano_only"].edits
        for p in e.pairs
        if p.opinions
    )

    report = write_report(
        out, labels_path=env.labels_path, edits_root=env.edits_root, skills_root=BENCH.skills
    )
    text = report.read_text(encoding="utf-8")
    assert "SIMULATED MODELS — pipeline smoke test; not benchmark results" in text
    assert (out / "cost_vs_recall.svg").is_file()
