"""Proxy metrics: calls, USD, prompt tokens and latency per tier and purpose.

``ratchet_model_cached_prompt_tokens_total`` over ``ratchet_model_prompt_tokens_total`` is
the prompt-cache share: the prompt tokens the upstream served from its prefix cache.

Each app owns its own ``CollectorRegistry`` so several apps (tests, ``ratchet serve``) can
coexist in one process without duplicate-metric errors.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram
from prometheus_client.exposition import generate_latest

__all__ = ["ProxyMetrics", "CONTENT_TYPE"]

CONTENT_TYPE = CONTENT_TYPE_LATEST
_LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, 60000, 120000, 300000)


class ProxyMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.calls = Counter(
            "ratchet_model_calls",
            "Model calls through the proxy.",
            ["tier", "purpose", "status"],
            registry=self.registry,
        )
        self.usd = Counter(
            "ratchet_model_usd",
            "USD spent on model calls.",
            ["tier", "purpose"],
            registry=self.registry,
        )
        self.prompt_tokens = Counter(
            "ratchet_model_prompt_tokens",
            "Prompt tokens billed on model calls.",
            ["tier", "purpose"],
            registry=self.registry,
        )
        self.cached_prompt_tokens = Counter(
            "ratchet_model_cached_prompt_tokens",
            "Prompt tokens the upstream served from its prompt cache.",
            ["tier", "purpose"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "ratchet_model_latency_ms",
            "Model call latency in milliseconds.",
            ["tier", "purpose"],
            buckets=_LATENCY_BUCKETS_MS,
            registry=self.registry,
        )

    def observe(
        self,
        *,
        tier: str,
        purpose: str,
        status: str,
        usd: float = 0.0,
        latency_ms: int = 0,
        prompt_tokens: int = 0,
        cached_prompt_tokens: int = 0,
    ) -> None:
        self.calls.labels(tier=tier, purpose=purpose, status=status).inc()
        if usd > 0:
            self.usd.labels(tier=tier, purpose=purpose).inc(usd)
        if prompt_tokens > 0:
            self.prompt_tokens.labels(tier=tier, purpose=purpose).inc(prompt_tokens)
        if cached_prompt_tokens > 0:
            self.cached_prompt_tokens.labels(tier=tier, purpose=purpose).inc(cached_prompt_tokens)
        if status in {"ok", "cached"}:
            self.latency.labels(tier=tier, purpose=purpose).observe(latency_ms)

    def render(self) -> bytes:
        return bytes(generate_latest(self.registry))
