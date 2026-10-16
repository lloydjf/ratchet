"""Orchestrator Prometheus metrics, one registry per process.

- ``ratchet_edit_duration_s{runner}``: edit detected → verdict decided, per verdict.
- ``ratchet_stage_duration_ms{stage}``: each stage of the verdict's timeline.
- ``ratchet_jobs_total{runner,status}``: replay batches by outcome — ``ok`` / ``partial`` /
  ``failed`` when a batch is imported, ``stalled`` when it misses its heartbeat,
  ``lost`` when it ended without a result and is retried, ``submit_failed`` when the runner
  refused it.
- ``ratchet_job_cold_start_s{runner}``: batch submitted → the worker's first heartbeat.

The orchestrator and the dashboard API share a process under ``ratchet serve``, so the API's
``/metrics`` appends :func:`render` to its own registry. A standalone orchestrator process
keeps its own copy (not scraped). The registry is built on first use (no import-time side
effects); :func:`reset` gives tests a fresh one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

from prometheus_client import CollectorRegistry, Counter, Histogram
from prometheus_client.exposition import generate_latest

from ratchet import clock
from ratchet.models import Verdict

__all__ = [
    "OrchestratorMetrics",
    "metrics",
    "render",
    "reset",
    "observe_verdict",
    "job_submitted",
    "job_heartbeat",
    "job_finished",
]

_EDIT_BUCKETS_S = (5, 10, 20, 30, 45, 60, 90, 120, 180, 300, 600, 1200)
_STAGE_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 20000, 30000, 60000, 120000)
_COLD_BUCKETS_S = (0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300)
_MAX_PENDING = 1024


@dataclass
class OrchestratorMetrics:
    registry: CollectorRegistry = field(default_factory=CollectorRegistry)
    _submitted: dict[str, tuple[str, datetime]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.edit_duration = Histogram(
            "ratchet_edit_duration_s",
            "Edit detected to verdict decided, in seconds.",
            ["runner"],
            buckets=_EDIT_BUCKETS_S,
            registry=self.registry,
        )
        self.stage_duration = Histogram(
            "ratchet_stage_duration_ms",
            "Verdict pipeline stage duration (verdict timeline stage keys), in milliseconds.",
            ["stage"],
            buckets=_STAGE_BUCKETS_MS,
            registry=self.registry,
        )
        self.jobs = Counter(
            "ratchet_jobs",
            "Replay batches (jobs) by runner and outcome.",
            ["runner", "status"],
            registry=self.registry,
        )
        self.cold_start = Histogram(
            "ratchet_job_cold_start_s",
            "Replay batch submitted to the worker's first heartbeat, in seconds.",
            ["runner"],
            buckets=_COLD_BUCKETS_S,
            registry=self.registry,
        )


@lru_cache(maxsize=1)
def metrics() -> OrchestratorMetrics:
    return OrchestratorMetrics()


def reset() -> None:
    metrics.cache_clear()


def render() -> bytes:
    return bytes(generate_latest(metrics().registry))


def _runner_label(runner: str) -> str:
    return runner or "none"


def observe_verdict(verdict: Verdict) -> None:
    m = metrics()
    m.edit_duration.labels(runner=_runner_label(verdict.runner)).observe(verdict.duration_s)
    for stage in verdict.stages:
        m.stage_duration.labels(stage=stage.stage).observe(stage.elapsed_ms)


def job_submitted(batch_id: str, runner: str) -> None:
    pending = metrics()._submitted
    if len(pending) >= _MAX_PENDING:  # never grows without bound (batches that never beat)
        pending.pop(next(iter(pending)))
    pending[batch_id] = (runner, clock.now())


def job_heartbeat(batch_id: str) -> None:
    """The worker reported progress: the first call per submission observes the cold start."""
    m = metrics()
    entry = m._submitted.pop(batch_id, None)
    if entry is None:
        return
    runner, submitted = entry
    seconds = max((clock.now() - submitted).total_seconds(), 0.0)
    m.cold_start.labels(runner=_runner_label(runner)).observe(seconds)


def job_finished(batch_id: str, runner: str, status: str) -> None:
    m = metrics()
    m._submitted.pop(batch_id, None)
    m.jobs.labels(runner=_runner_label(runner), status=status).inc()
