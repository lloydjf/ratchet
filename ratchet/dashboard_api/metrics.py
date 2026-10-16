"""``/metrics`` for the dashboard API, Prometheus text format, one registry per app.

- ``ratchet_api_requests_total{route,method,status}`` and ``ratchet_api_request_duration_ms
  {route}``: incremented by the request middleware (route = the path template).
- ``ratchet_edits_total{decision}``: derived at scrape time from the ``verdicts`` table, so
  it counts every verdict the orchestrator saved, whichever process saved it.
- ``ratchet_simulate_runs_total{scenario,status}``: derived at scrape time from the
  ``simulate.started`` (status ``started``) and ``simulate.finished`` (payload ``status``)
  events.
- ``ratchet_up{component}``: 1/0 for ``dashboard_api``, ``db``, ``orchestrator``, ``simulate``.
- ``ratchet_batches_stalled``: unfinished replay batches with status ``stalled`` or no
  heartbeat for :data:`STALL_S` (the "Jobs stalled" alert; also catches a dead orchestrator);
  ``ratchet_batch_stalls_total``: ``batch.stalled`` events ever.
- ``ratchet_simulate_recent_failed`` / ``ratchet_simulate_recent_runs``: failed runs among
  the last :data:`RECENT_SIMULATE` ``simulate.finished`` events (the "Simulate failing"
  alert: ≥ 2 of the last 5).
- The orchestrator's own registry (``ratchet_edit_duration_s``, ``ratchet_stage_duration_ms``,
  ``ratchet_jobs_total``, ``ratchet_job_cold_start_s``; ``orchestrator.prom``) is appended to
  the scrape: under ``ratchet serve`` both live in one process.

Model calls and spend (``ratchet_model_*``) are on the proxy's ``/metrics`` (:8081), not here.

Deriving the edit and Simulate counters from the store keeps them correct across restarts
and when the orchestrator runs in another process; they only grow because neither table is
pruned.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from datetime import timedelta

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.exposition import generate_latest

from ratchet import clock
from ratchet.store import Store

__all__ = ["ApiMetrics", "CONTENT_TYPE", "STALL_S", "RECENT_SIMULATE"]

CONTENT_TYPE = CONTENT_TYPE_LATEST
_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)
STALL_S = 120  # no heartbeat for 120 s
RECENT_SIMULATE = 5
_FINISHED = ("ok", "partial", "failed")


def _ops_metrics(store: Store) -> list[Metric]:
    cutoff = clock.iso(clock.now() - timedelta(seconds=STALL_S))
    stalled = store.query_one(
        f"""SELECT COUNT(*) AS n FROM batches b JOIN edits e ON e.edit_id = b.edit_id
            WHERE b.status NOT IN ({",".join("?" * len(_FINISHED))})
              AND e.state NOT IN ('done', 'failed_infra')
              AND (b.status = 'stalled'
                   OR (b.last_heartbeat_at IS NOT NULL AND b.last_heartbeat_at < ?))""",
        (*_FINISHED, cutoff),
    )
    stalls = store.query_one("SELECT COUNT(*) AS n FROM events WHERE kind = 'batch.stalled'")
    recent = store.query(
        """SELECT COALESCE(json_extract(payload_json, '$.status'), '') AS status
           FROM events WHERE kind = 'simulate.finished' ORDER BY id DESC LIMIT ?""",
        (RECENT_SIMULATE,),
    )
    gauge = GaugeMetricFamily(
        "ratchet_batches_stalled",
        f"Unfinished replay batches marked stalled or silent for over {STALL_S} s.",
        value=float(stalled["n"]) if stalled else 0.0,
    )
    total = CounterMetricFamily(
        "ratchet_batch_stalls",
        "Replay batches the orchestrator marked stalled (batch.stalled events).",
        value=float(stalls["n"]) if stalls else 0.0,
    )
    failed = GaugeMetricFamily(
        "ratchet_simulate_recent_failed",
        f"Failed runs among the last {RECENT_SIMULATE} finished Simulate runs.",
        value=float(sum(str(r["status"]) == "failed" for r in recent)),
    )
    runs = GaugeMetricFamily(
        "ratchet_simulate_recent_runs",
        f"Finished Simulate runs considered (at most {RECENT_SIMULATE}).",
        value=float(len(recent)),
    )
    return [gauge, total, failed, runs]


class _StoreCollector:
    def __init__(self, store: Callable[[], Store], up: Callable[[], dict[str, bool]]) -> None:
        self._store = store
        self._up = up

    def describe(self) -> list[Metric]:
        return []

    def collect(self) -> Iterator[Metric]:
        edits = CounterMetricFamily(
            "ratchet_edits", "Verdicts saved, by decision.", labels=["decision"]
        )
        runs = CounterMetricFamily(
            "ratchet_simulate_runs",
            "Simulate runs, by scenario and status.",
            labels=["scenario", "status"],
        )
        db_ok = True
        ops: list[Metric] = []
        try:
            store = self._store()
            for row in store.query(
                "SELECT decision, COUNT(*) AS n FROM verdicts GROUP BY decision"
            ):
                edits.add_metric([str(row["decision"])], float(row["n"]))
            for row in store.query(
                """SELECT COALESCE(json_extract(payload_json, '$.scenario'), '') AS scenario,
                          CASE WHEN kind = 'simulate.started' THEN 'started'
                               ELSE COALESCE(json_extract(payload_json, '$.status'), 'unknown')
                          END AS status, COUNT(*) AS n
                   FROM events WHERE kind IN ('simulate.started', 'simulate.finished')
                   GROUP BY scenario, status"""
            ):
                runs.add_metric([str(row["scenario"]), str(row["status"])], float(row["n"]))
            ops = _ops_metrics(store)
        except (sqlite3.Error, OSError):
            db_ok = False
        up = GaugeMetricFamily(
            "ratchet_up", "Component is up (1) or not (0).", labels=["component"]
        )
        for component, ok in {**self._up(), "db": db_ok}.items():
            up.add_metric([component], 1.0 if ok else 0.0)
        yield edits
        yield runs
        yield from ops
        yield up


class ApiMetrics:
    def __init__(self, store: Callable[[], Store], up: Callable[[], dict[str, bool]]) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "ratchet_api_requests",
            "Dashboard API requests.",
            ["route", "method", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "ratchet_api_request_duration_ms",
            "Dashboard API request latency in milliseconds.",
            ["route"],
            buckets=_BUCKETS_MS,
            registry=self.registry,
        )
        self.registry.register(_StoreCollector(store, up))

    def observe(self, *, route: str, method: str, status: int, latency_ms: float) -> None:
        self.requests.labels(route=route, method=method, status=str(status)).inc()
        self.latency.labels(route=route).observe(latency_ms)

    def render(self) -> bytes:
        """Blocking (queries SQLite); call it from a worker thread. Appends this process's
        orchestrator registry (``orchestrator.prom``)."""
        from ratchet.orchestrator import prom

        return bytes(generate_latest(self.registry)) + prom.render()
