"""Simulate scenarios 1–3 cold, concurrently in three sessions: each finishes in
under 60 s with its expected decision, sessions never see each other's events or repos,
and auto-reset deletes the session repos while the verdicts stay readable."""

from __future__ import annotations

import asyncio
import time

import pytest
from e2e_support import Demo, orchestrator_for

from ratchet.orchestrator.progress import progress_for
from ratchet.orchestrator.simulate import SimulateService, session_root

pytestmark = pytest.mark.e2e

EXPECTED = {"1": "BLOCK", "2": "BLOCK", "3": "PASS"}


async def _run(service: SimulateService, demo: Demo, k: str) -> tuple[str, float, set[str]]:
    started = time.monotonic()
    start = await service.start(k, f"{k}" * 16)
    assert start.status == "running" and start.edit_id and start.verdict_id
    stages: set[str] = set()
    while (
        progress := await asyncio.to_thread(progress_for, demo.store, start.edit_id)
    ) is not None:
        stages.add(progress.stage)
        await asyncio.sleep(0.1)
    return start.verdict_id, time.monotonic() - started, stages


async def test_simulate_three_scenarios_cold_isolated_and_reset(demo: Demo) -> None:
    demo = demo.with_settings(ratchet_max_edits_in_flight=3, ratchet_simulate_reset_s=0)
    orchestrator = orchestrator_for(demo)
    await orchestrator.start(watch=False)
    try:
        service = SimulateService(demo.settings, demo.store, orchestrator)
        assert [s.scenario_id for s in service.scenarios()] == ["1", "2", "3"]
        results = await asyncio.gather(*(_run(service, demo, k) for k in EXPECTED))
        await orchestrator.wait_idle(60)
        for (verdict_id, elapsed, stages), (k, decision) in zip(
            results, EXPECTED.items(), strict=True
        ):
            verdict = demo.store.load_verdict(verdict_id)
            assert verdict.decision == decision, (k, verdict.headline)
            assert elapsed < 60, (k, elapsed)
            assert verdict.session_hash == k * 16 and verdict.simulated_models
            assert "replaying" in stages
            mine = demo.store.events(session_hash=k * 16, include_global=False, limit=1000)
            assert mine and all(e.session_hash == k * 16 for e in mine)
            assert {"simulate.started", "simulate.finished"} <= {e.kind for e in mine}
            assert session_root(demo.store, k * 16).is_dir()
        operator_main = demo.repo.main_head()
        assert operator_main == demo.repo.candidate_head()  # the operator repo never moved
        assert await service.reset_expired() == 3
        for k in EXPECTED:
            assert not session_root(demo.store, k * 16).exists()
            assert demo.store.load_verdict(results[int(k) - 1][0]).decision == EXPECTED[k]
        assert {r.status for r in demo.store.sessions()} == {"reset"}
        again = await service.start("1", "1" * 16)  # a re-run gets a fresh edit
        assert again.edit_id != demo.store.load_verdict(results[0][0]).edit_id
        await orchestrator.wait_idle(60)
    finally:
        await orchestrator.stop()


async def test_scenario_one_matches_three_times_in_a_row(demo: Demo) -> None:
    """`ratchet simulate 1 --repeat 3` in sim mode is 3/3, each run in a fresh session — and
    it runs with RATCHET_SIMULATE_ENABLED off (the flag gates the HTTP route only)."""
    from ratchet.cli.simulate_cmd import simulate_repeat

    demo = demo.with_settings(ratchet_simulate_enabled=False)
    result = await simulate_repeat(demo.settings, demo.store, "1", repeat=3, echo=False)
    assert result.all_matched, [o.verdict.headline for o in result.outcomes]
    assert len({o.verdict.session_hash for o in result.outcomes}) == 3
    assert len({o.verdict.edit_id for o in result.outcomes}) == 3
    assert all(o.verdict.decision == "BLOCK" and o.elapsed_s < 60 for o in result.outcomes)
    assert result.p95_s >= result.mean_s > 0
