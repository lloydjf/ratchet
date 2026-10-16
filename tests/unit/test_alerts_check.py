"""deploy/vm/alerts_check.py: which registry each alert reads."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PATH = Path(__file__).resolve().parents[2] / "deploy" / "vm" / "alerts_check.py"
DASH, PROXY = "http://127.0.0.1:8080", "http://127.0.0.1:8081"


@pytest.fixture(scope="module")
def checks() -> ModuleType:
    spec = importlib.util.spec_from_file_location("alerts_check", PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["alerts_check"] = module
    spec.loader.exec_module(module)
    return module


def _fetcher(pages: dict[str, str]) -> Any:
    def fetch(url: str) -> tuple[int, str]:
        if url not in pages:
            raise OSError("connection refused")
        return 200, pages[url]

    return fetch


def _pages(*, usd: float, dash: str = "") -> dict[str, str]:
    proxy = f'ratchet_model_usd_total{{tier="super",purpose="agent"}} {usd}\n'
    return {f"{DASH}/healthz": "{}", f"{PROXY}/metrics": proxy, f"{DASH}/metrics": dash}


def _run(checks: ModuleType, pages: dict[str, str], state: dict[str, Any], now: float) -> list[str]:
    return list(
        checks.evaluate(
            _fetcher(pages),
            dashboard_url=DASH,
            proxy_url=PROXY,
            disk_pct=10.0,
            srv="/srv/ratchet",
            state=state,
            now=now,
        )
    )


def test_spend_spike_reads_the_proxy_registry(checks: ModuleType) -> None:
    state: dict[str, Any] = {}
    assert _run(checks, _pages(usd=1.0), state, 1000.0) == []
    out = _run(checks, _pages(usd=7.5), state, 1600.0)
    assert out == [
        "ALERT spend_spike: model spend +$6.50 in the last hour (> $5); check by purpose"
    ]


def test_spend_spike_survives_a_proxy_restart(checks: ModuleType) -> None:
    assert checks.spend_rise([[0, 4.0], [60, 4.5], [120, 0.2], [180, 1.0]]) == pytest.approx(1.5)


def test_simulate_failing_fires_on_two_of_the_last_five(checks: ModuleType) -> None:
    state: dict[str, Any] = {}
    dash = "ratchet_simulate_recent_failed 2.0\nratchet_simulate_recent_runs 5.0\n"
    out = _run(checks, _pages(usd=0, dash=dash), state, 1000.0)
    assert any(line.startswith("ALERT simulate_failing: 2 of the last 5") for line in out)
    calm = "ratchet_simulate_recent_failed 1.0\nratchet_simulate_recent_runs 5.0\n"
    assert "RECOVERED simulate_failing" in _run(checks, _pages(usd=0, dash=calm), state, 1100.0)


def test_jobs_stalled_fires_on_a_silent_batch_or_a_new_stall(checks: ModuleType) -> None:
    state: dict[str, Any] = {}
    quiet = "ratchet_batches_stalled 0.0\nratchet_batch_stalls_total 3.0\n"
    assert _run(checks, _pages(usd=0, dash=quiet), state, 1000.0) == []  # baseline only
    retried = "ratchet_batches_stalled 0.0\nratchet_batch_stalls_total 4.0\n"
    out = _run(checks, _pages(usd=0, dash=retried), state, 1120.0)
    assert any(line.startswith("ALERT jobs_stalled") for line in out)
    silent = "ratchet_batches_stalled 1.0\nratchet_batch_stalls_total 4.0\n"
    assert _run(checks, _pages(usd=0, dash=silent), state, 1240.0) == []  # still active, no repeat
    assert _run(checks, _pages(usd=0, dash=quiet.replace("3.0", "4.0")), state, 1360.0) == [
        "RECOVERED jobs_stalled"
    ]


def test_demo_down_after_two_failed_health_checks(checks: ModuleType) -> None:
    state: dict[str, Any] = {}
    assert _run(checks, {}, state, 1000.0) == []
    out = _run(checks, {}, state, 1120.0)
    assert out == [
        "ALERT demo_down: /healthz failed 2x; run deploy/vm/restart.sh, then failover.sh"
    ]


def test_metric_sum_adds_every_label_set(checks: ModuleType) -> None:
    text = 'a_total{x="1"} 1.5\na_total{x="2"} 2\n# HELP a_total x\nab_total 9\n'
    assert checks.metric_sum(text, "a_total") == 3.5
    assert checks.metric_sum(text, "missing") is None
