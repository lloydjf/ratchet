"""The orchestrator's Prometheus series (edit/stage durations, jobs, cold start)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta

import pytest
from prometheus_client import CollectorRegistry

from ratchet import clock
from ratchet.models import StageTiming, Verdict
from ratchet.orchestrator import prom


@pytest.fixture(autouse=True)
def fresh() -> Iterator[None]:
    prom.reset()
    yield
    prom.reset()


def _value(registry: CollectorRegistry, name: str, labels: dict[str, str]) -> float | None:
    return registry.get_sample_value(name, labels)


def _verdict(runner: str) -> Verdict:
    now = clock.now()
    return Verdict(
        verdict_id="verdict_1",
        edit_id="edit_1",
        pairs=[],
        decision="PASS",
        action="merged",
        narrative="n",
        cost={},
        duration_s=42,
        created_at=now,
        runner=runner,
        stages=[
            StageTiming(stage="selecting", started_at=now, elapsed_ms=120),
            StageTiming(stage="replaying", started_at=now, elapsed_ms=30_000),
        ],
    )


def test_a_verdict_observes_edit_and_stage_durations() -> None:
    prom.observe_verdict(_verdict("local"))
    prom.observe_verdict(_verdict(""))
    reg = prom.metrics().registry
    assert _value(reg, "ratchet_edit_duration_s_sum", {"runner": "local"}) == 42
    assert _value(reg, "ratchet_edit_duration_s_count", {"runner": "none"}) == 1
    assert _value(reg, "ratchet_stage_duration_ms_sum", {"stage": "replaying"}) == 60_000
    assert _value(reg, "ratchet_stage_duration_ms_count", {"stage": "selecting"}) == 2


def test_cold_start_is_observed_once_per_submission(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = clock.now()
    monkeypatch.setattr(clock, "now", lambda: t0)
    prom.job_submitted("batch_a", "nebius")
    monkeypatch.setattr(clock, "now", lambda: t0 + timedelta(seconds=7))
    prom.job_heartbeat("batch_a")
    prom.job_heartbeat("batch_a")  # later beats do not count again
    reg = prom.metrics().registry
    assert _value(reg, "ratchet_job_cold_start_s_count", {"runner": "nebius"}) == 1
    assert _value(reg, "ratchet_job_cold_start_s_sum", {"runner": "nebius"}) == 7


def test_jobs_are_counted_by_runner_and_status() -> None:
    prom.job_submitted("batch_a", "local")
    prom.job_finished("batch_a", "local", "stalled")
    prom.job_heartbeat("batch_a")  # a stalled submission is no longer pending
    prom.job_finished("batch_a", "local-retry", "ok")
    reg = prom.metrics().registry
    assert _value(reg, "ratchet_jobs_total", {"runner": "local", "status": "stalled"}) == 1
    assert _value(reg, "ratchet_jobs_total", {"runner": "local-retry", "status": "ok"}) == 1
    assert _value(reg, "ratchet_job_cold_start_s_count", {"runner": "local"}) is None
    assert b"ratchet_jobs_total" in prom.render()
