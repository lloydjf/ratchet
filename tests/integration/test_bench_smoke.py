"""Bench smoke: 10+ edits through the runner (report generated, metrics non-null) and E6
(unchanged-skill replays ×3: pair pass rate recorded) on the offline simulator."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ratchet.bench.dataset import BenchPaths
from ratchet.bench.metrics import summarize
from ratchet.bench.results import load_results
from ratchet.bench.smoke import SmokeOptions, run_smoke
from ratchet.config.settings import Settings, reset_settings

pytestmark = pytest.mark.e2e

BENCH = BenchPaths(Path(__file__).resolve().parents[2] / "bench")


def test_bench_smoke_runs_ten_edits_and_the_noise_floor(tmp_path: Path) -> None:
    opts = SmokeOptions(work=tmp_path / "work", out=tmp_path / "out", configs=["noise", "cascade"])
    try:
        asyncio.run(
            run_smoke(BENCH, Settings(_env_file=None), opts, echo=lambda _: None)  # type: ignore[call-arg]
        )
    finally:
        reset_settings()
    results = load_results(opts.out / "results.json")
    assert results is not None and results.meta.simulated
    cascade = results.runs["cascade"].edits
    assert len(cascade) >= 10 and all(e.error is None for e in cascade)
    m = summarize(cascade, noise=results.runs["noise"].edits)
    assert m.recall.value is not None and m.false_block.value is not None
    assert m.cost_total.mean is not None and m.latency.mean is not None
    assert m.noise is not None and m.noise.pass_rate is not None  # E6 noise floor
    assert {e.repeat for e in results.runs["noise"].edits} == {1, 2, 3}
    report = (opts.out / "report.md").read_text(encoding="utf-8")
    assert "SIMULATED MODELS — pipeline smoke test; not benchmark results" in report
    assert (opts.out / "labels.json").is_file() and (opts.out / "cost_vs_recall.svg").is_file()
