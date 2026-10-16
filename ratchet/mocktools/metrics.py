"""Prometheus metrics for the mock tool server, on a per-app registry."""

from __future__ import annotations

from typing import Literal

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

__all__ = ["CassetteEvent", "CassetteMetrics", "CONTENT_TYPE"]

# Miss kinds, plus miss_write: a write_reversible miss answered with a synthetic success.
CassetteEvent = Literal["hit", "tolerant_hit", "miss_read", "miss_write", "blocked", "recorded"]
CONTENT_TYPE: str = CONTENT_TYPE_LATEST


class CassetteMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self._events = Counter(
            "ratchet_cassette_events_total",
            "Mock tool server cassette events by kind.",
            ["kind"],
            registry=self.registry,
        )
        self._counts: dict[str, int] = {}

    def inc(self, kind: CassetteEvent) -> None:
        self._events.labels(kind=kind).inc()
        self._counts[kind] = self._counts.get(kind, 0) + 1

    def count(self, kind: CassetteEvent) -> int:
        return self._counts.get(kind, 0)

    def render(self) -> bytes:
        return bytes(generate_latest(self.registry))
