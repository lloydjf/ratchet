"""Runner planning: resume (skip done edits), stratified --limit, noise jobs, replay reuse,
the single-judge decision for E4, the budget guard and the cost estimate."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_fixtures import T0, edit, pair, run, small_results  # noqa: E402

from ratchet.bench import estimate  # noqa: E402
from ratchet.bench.dataset import BenchPaths, load_config  # noqa: E402
from ratchet.bench.env import BenchEnv  # noqa: E402
from ratchet.bench.labels import EditLabel, LabelSet, save_labels, write_edit  # noqa: E402
from ratchet.bench.results import bench_decision  # noqa: E402
from ratchet.bench.runner import RunOptions, _guard, noise_labels, plan_jobs, select_labels  # noqa: E402
from ratchet.config.settings import Settings  # noqa: E402
from ratchet.errors import BudgetExceeded, ConfigError  # noqa: E402
from ratchet.models import JudgeOpinion, Level0Finding, PairVerdict, Verdict  # noqa: E402

BENCH = Path(__file__).resolve().parents[2] / "bench"


def _lab(edit_id: str, type_: str) -> EditLabel:
    return EditLabel.model_validate(
        {
            "edit_id": edit_id,
            "skill": "expense-report",
            "type": type_,
            "expected_decision": "PASS" if type_ in ("N0", "D1") else "BLOCK",
        }
    )


def _env(tmp_path: Path, **overrides: object) -> BenchEnv:
    base = Settings(_env_file=None, ratchet_upstream="sim", **overrides)  # type: ignore[call-arg]
    return BenchEnv.open(
        BenchPaths(BENCH),
        tmp_path / "work",
        base,
        edits_root=tmp_path / "edits",
        labels_path=tmp_path / "labels.json",
        tavily_dir=tmp_path / "tavily",
    )


def test_select_labels_is_seeded_and_stratified_by_type() -> None:
    labels = [_lab(f"r{t}-{i}", f"R{t}") for t in (1, 2) for i in range(5)] + [_lab("n0-1", "N0")]
    first = select_labels(labels, types=None, limit=3, seed=1)
    assert [lab.type for lab in first] == ["N0", "R1", "R2"]
    assert first == select_labels(labels, types=None, limit=3, seed=1)
    assert {lab.type for lab in select_labels(labels, types=["R2"], limit=None, seed=0)} == {"R2"}
    assert len(select_labels(labels, types=None, limit=None, seed=0)) == len(labels)


def test_noise_labels_repeat_every_skill() -> None:
    jobs = noise_labels(["a", "b"], 3)
    assert [(lab.edit_id, k) for lab, k in jobs][:3] == [
        ("noise-a-1", 1),
        ("noise-b-1", 1),
        ("noise-a-2", 2),
    ]
    assert len(jobs) == 6


def test_plan_jobs_resumes_and_reuses_the_primary_runs_replays(tmp_path: Path) -> None:
    env = _env(tmp_path)
    files = {"expense-report/SKILL.md": "x\n"}
    for edit_id in ("r2-a", "r4-a", "n0-a"):
        write_edit(env.edits_root, edit_id, files)
    save_labels(
        env.labels_path,
        LabelSet(labels=[_lab("r2-a", "R2"), _lab("r4-a", "R4"), _lab("n0-a", "N0")]),
    )
    results = small_results()
    primary = results.runs["cascade"].edits
    results.runs["cascade"] = run(
        "cascade",
        "cascade",
        [e.model_copy(update={"edit_id": f"edit_{i}"}) for i, e in enumerate(primary)],
    )
    cascade = load_config(BENCH / "configs" / "cascade.yaml")
    opts = RunOptions(config_path=BENCH / "configs" / "cascade.yaml", out=tmp_path)
    # r2-a, r4-a and n0-a are already in the cascade run: nothing left to do.
    assert plan_jobs(env, cascade, results, opts) == []
    del results.runs["cascade"].edits[0]  # r2-a "crashed": it runs again
    assert [j.label.edit_id for j in plan_jobs(env, cascade, results, opts)] == ["r2-a"]
    nano = load_config(BENCH / "configs" / "nano_only.yaml")
    del results.runs["nano_only"]
    jobs = plan_jobs(env, nano, results, opts)
    by_id = {j.label.edit_id: j.source_edit_id for j in jobs}
    assert by_id == {"r2-a": None, "r4-a": "edit_1", "n0-a": "edit_4"}
    assert jobs[0].spec.files == files


def test_plan_jobs_in_noise_mode_skips_finished_repeats(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.save_manifest({"exp-berlin-trip": "run_1"})
    results = small_results()
    noise = load_config(BENCH / "configs" / "noise.yaml")
    opts = RunOptions(config_path=BENCH / "configs" / "noise.yaml", out=tmp_path)
    jobs = plan_jobs(env, noise, results, opts)
    assert [j.label.edit_id for j in jobs] == ["noise-expense-report-3"]
    assert jobs[0].spec.noise and jobs[0].repeat == 3


def _verdict(pairs: list[PairVerdict]) -> Verdict:
    return Verdict(
        verdict_id="verdict_1",
        edit_id="edit_1",
        pairs=pairs,
        decision="WARN",
        action="held",
        narrative="",
        cost={},
        duration_s=1,
        created_at=datetime(2026, 10, 16, tzinfo=UTC),
    )


def test_single_judge_regression_blocks_only_in_single_tier_modes() -> None:
    finding = Level0Finding(
        golden_run_id="g",
        replay_run_id="r",
        new_calls=[],
        missing_calls=[],
        changed_args=[],
        hard_fail=False,
    )
    opinion = JudgeOpinion(
        tier="nano", verdict="regressed", confidence=0.9, reasons=["x"], evidence_steps=[], usd=0.0
    )
    p = PairVerdict(
        golden_run_id="g",
        replay_run_id="r",
        level0=finding,
        opinions=[opinion],
        drift=[],
        outcome="regression",
        usd=0.0,
        decided_by="nano",
    )
    verdict = _verdict([p])
    assert bench_decision(verdict, single_judge_blocks=True) == "BLOCK"
    assert bench_decision(verdict, single_judge_blocks=False) == "WARN"


def test_guard_refuses_live_spend_over_the_cap_or_without_confirmation(tmp_path: Path) -> None:
    env = _env(tmp_path, ratchet_bench_usd_cap=1.0)
    live = BenchEnv(
        **{**env.__dict__, "settings": env.settings.model_copy(update={"ratchet_upstream": "live"})}
    )
    big, small = estimate.Estimate("big"), estimate.Estimate("small")
    big.add("x", 1, 5.0)
    small.add("x", 1, 0.1)
    out: list[str] = []
    with pytest.raises(BudgetExceeded):
        _guard(live, big, out.append, lambda _: True)
    with pytest.raises(ConfigError):
        _guard(live, small, out.append, lambda _: False)
    _guard(live, small, out.append, lambda _: True)
    _guard(env, big, out.append, lambda _: False)  # simulated: printed, never refused
    assert any("simulated" in line for line in out)


def test_estimates_scale_with_mode_and_reuse() -> None:
    assert estimate.judge_usd_per_pair("nano_only") < estimate.judge_usd_per_pair("cascade")
    assert estimate.judge_usd_per_pair("cascade") < estimate.judge_usd_per_pair("ultra_only")
    fresh = estimate.sweep_estimate(
        name="c",
        n_edits=10,
        pairs_per_edit=15,
        mode="cascade",
        drift=True,
        replay_usd=0.01,
        replay_source="x",
        reuse_replays=False,
    )
    reused = estimate.sweep_estimate(
        name="c",
        n_edits=10,
        pairs_per_edit=15,
        mode="cascade",
        drift=True,
        replay_usd=0.01,
        replay_source="x",
        reuse_replays=True,
    )
    assert fresh.total - reused.total == pytest.approx(10 * 15 * 0.01)
    assert "total" in fresh.render()
    build = estimate.build_estimate(
        n_goldens=100, n_inject_variants=120, replays_per_variant=15, n_d1=6, replay_usd=0.01
    )
    assert build.total > 0 and len(build.lines) == 5
    assert edit("e", "R1", "BLOCK", [pair("t", "pass")]).judge_usd > 0 and T0.year == 2026


def test_build_estimate_counts_only_missing_work(tmp_path: Path) -> None:
    from ratchet.bench.build import BuildPlan
    from ratchet.bench.cli_support import build_plan_estimate
    from ratchet.bench.dataset import load_tasks

    env = _env(tmp_path)
    tasks = [
        t for t in load_tasks(BENCH / "tasks.yaml") if t.expected_skill == "fx-expense-converter"
    ]
    env.save_manifest({tasks[0].task_id: "run_x"})
    save_labels(env.labels_path, LabelSet(labels=[_lab("r1-fx-expense-converter-1", "R1")]))
    plan = BuildPlan(tasks=tasks, skills=["fx-expense-converter"], types=("R1", "R2"))
    lines = {line.item: line.count for line in build_plan_estimate(env, plan).lines}
    assert lines["golden recordings (agent, super)"] == 9  # one golden already recorded
    assert lines["inject_regression calls (ultra)"] == pytest.approx(3 * 1.3)  # 4 variants - 1 done
    assert lines["D1 manifestation replays (agent, super)"] == 2 * 15
