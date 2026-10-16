"""In-VM alert checks, run by deploy/vm/alerts.sh every 2 minutes.

Standard library only (it runs with the system python3, outside the app's venv). Two
Prometheus registries are scraped (docs/RUNBOOK.md "Metrics"):

- dashboard ``/metrics`` (:8080): ``ratchet_up``, ``ratchet_batches_stalled``,
  ``ratchet_batch_stalls_total``, ``ratchet_simulate_recent_failed``, orchestrator metrics;
- proxy ``/metrics`` (:8081): ``ratchet_model_usd_total`` and the other ``ratchet_model_*``.

Alerts, each at most once per 30 min while active, plus a recovery line:

    demo_down         /healthz failed 2 runs in a row
    disk              the /srv/ratchet filesystem > 80 % used
    spend_spike       ratchet_model_usd_total rose > $5 within 1 h (proxy registry)
    simulate_failing  >= 2 of the last 5 Simulate runs failed
    jobs_stalled      a replay batch had no heartbeat for 120 s (or was marked stalled)

Usage: ``alerts_check.py <dashboard_url> <proxy_url> <srv_dir> <state.json>``; prints one
``ALERT kind: text`` / ``RECOVERED kind`` line per message.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
import urllib.request
from collections.abc import Callable
from typing import Any

__all__ = [
    "SPEND_LIMIT_USD",
    "metric_sum",
    "spend_rise",
    "Alerts",
    "evaluate",
    "main",
]

SPEND_LIMIT_USD = 5.0
SIMULATE_FAILS = 2
REPEAT_S = 1800
_SAMPLE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+(?P<value>\S+)", re.M)

Fetch = Callable[[str], tuple[int, str]]


def metric_sum(text: str, name: str) -> float | None:
    """Sum of every sample of ``name`` (all label sets); ``None`` when absent."""
    values = [float(m["value"]) for m in _SAMPLE.finditer(text) if m["name"] == name]
    return sum(values) if values else None


def spend_rise(history: list[list[float]]) -> float:
    """USD added over ``[[ts, counter], ...]``; a drop means the proxy restarted (counter
    reset), so the new value counts as spend since the restart."""
    rise = 0.0
    for (_, before), (_, after) in zip(history, history[1:], strict=False):
        rise += after - before if after >= before else after
    return rise


class Alerts:
    def __init__(self, state: dict[str, Any], now: float) -> None:
        self.state = state
        self.now = now
        self.out: list[str] = []

    def fire(self, kind: str, text: str, active: bool) -> None:
        last = self.state.setdefault("last_alert", {})
        was = self.state.setdefault("active", {}).get(kind, False)
        if active and (not was or self.now - last.get(kind, 0) > REPEAT_S):
            self.out.append(f"ALERT {kind}: {text}")
            last[kind] = self.now
        elif not active and was:
            self.out.append(f"RECOVERED {kind}")
        self.state["active"][kind] = active


def _get(fetch: Fetch, url: str) -> str | None:
    try:
        status, body = fetch(url)
    except Exception:  # noqa: BLE001 - any network failure is "unavailable"
        return None
    return body if status == 200 else None


def _spend(alerts: Alerts, proxy_text: str | None) -> None:
    usd = metric_sum(proxy_text, "ratchet_model_usd_total") if proxy_text else None
    if usd is None:
        return
    state, now = alerts.state, alerts.now
    hist = [h for h in state.get("usd", []) if now - h[0] <= 3600] + [[now, usd]]
    state["usd"] = hist
    rise = spend_rise(hist)
    alerts.fire(
        "spend_spike",
        f"model spend +${rise:.2f} in the last hour (> ${SPEND_LIMIT_USD:.0f}); check by purpose",
        rise > SPEND_LIMIT_USD,
    )


def _dashboard(alerts: Alerts, text: str | None) -> None:
    if text is None:
        return
    failed = metric_sum(text, "ratchet_simulate_recent_failed")
    runs = metric_sum(text, "ratchet_simulate_recent_runs") or 0
    if failed is not None:
        alerts.fire(
            "simulate_failing",
            f"{failed:.0f} of the last {runs:.0f} Simulate runs failed; ratchet logs --edit "
            "<id>, then RATCHET_SIMULATE_MODE=replayed",
            failed >= SIMULATE_FAILS,
        )
    stalled = metric_sum(text, "ratchet_batches_stalled")
    stalls = metric_sum(text, "ratchet_batch_stalls_total")
    seen = alerts.state.get("batch_stalls")
    new_stalls = stalls is not None and seen is not None and stalls > seen
    if stalls is not None:
        alerts.state["batch_stalls"] = stalls
    if stalled is not None:
        alerts.fire(
            "jobs_stalled",
            f"{stalled:.0f} replay batch(es) without a heartbeat for 120 s; check the runner "
            "(ratchet logs --edit <id>)",
            stalled > 0 or new_stalls,
        )


def evaluate(
    fetch: Fetch,
    *,
    dashboard_url: str,
    proxy_url: str,
    disk_pct: float,
    srv: str,
    state: dict[str, Any],
    now: float,
) -> list[str]:
    alerts = Alerts(state, now)
    healthy = _get(fetch, dashboard_url + "/healthz") is not None
    fails = 0 if healthy else int(state.get("health_fails", 0)) + 1
    state["health_fails"] = fails
    alerts.fire(
        "demo_down",
        f"/healthz failed {fails}x; run deploy/vm/restart.sh, then failover.sh",
        fails >= 2,
    )
    alerts.fire(
        "disk", f"{srv} at {disk_pct:.0f}% (> 80%); ratchet purge --before <date>", disk_pct > 80
    )
    if healthy:
        _spend(alerts, _get(fetch, proxy_url + "/metrics"))
        _dashboard(alerts, _get(fetch, dashboard_url + "/metrics"))
    return alerts.out


def _fetch(url: str) -> tuple[int, str]:
    with urllib.request.urlopen(url, timeout=5) as res:  # noqa: S310 - local URLs only
        return int(res.status), res.read().decode()


def main(argv: list[str]) -> int:
    dashboard_url, proxy_url, srv, state_path = argv[1:5]
    try:
        with open(state_path, encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        state = {}
    usage = shutil.disk_usage(srv)
    out = evaluate(
        _fetch,
        dashboard_url=dashboard_url.rstrip("/"),
        proxy_url=proxy_url.rstrip("/"),
        disk_pct=100 * usage.used / usage.total,
        srv=srv,
        state=state,
        now=time.time(),
    )
    with open(state_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
