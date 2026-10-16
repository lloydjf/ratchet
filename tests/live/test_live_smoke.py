"""Live smoke tests: real Token Factory and Tavily, cents of spend.

Marked ``live``: deselected by the default ``addopts`` and run only by the manual live
workflow (``uv run pytest -m live tests/live``). Each test is skipped unless the real
settings (``.env`` / environment) carry the key it needs; ``tests/conftest.py`` leaves
``live`` tests un-isolated so those settings are visible. Spend goes through the capture
proxy into a throwaway store under ``tmp_path``, so cost rows are checked, not assumed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ratchet.config.settings import Settings
from ratchet.judge.tavily import LiveTavily, SearchParams
from ratchet.llm.client import ModelClient
from ratchet.models import TIERS, DriftCheck
from ratchet.orchestrator.context import InProcessProxy
from ratchet.store import Store
from ratchet.util import new_id

pytestmark = pytest.mark.live

_PROMPT: list[dict[str, object]] = [
    {"role": "user", "content": "Reply with the single word: ready"}
]


def _live_settings(tmp_path: Path) -> Settings:
    settings = Settings()
    return settings.model_copy(
        update={
            "ratchet_upstream": "live",
            "ratchet_tavily_mode": "live",
            "ratchet_data_dir": tmp_path / "data",
        }
    )


async def test_each_tier_answers_a_short_prompt_via_the_proxy(tmp_path: Path) -> None:
    """Each tier answers a 20-token prompt through the proxy; cost rows written."""
    settings = _live_settings(tmp_path)
    if settings.nebius_token_factory_key is None:
        pytest.skip("NEBIUS_TOKEN_FACTORY_KEY is not set")
    store = Store.open(settings.ratchet_data_dir)
    proxy = InProcessProxy(settings, store)
    client = ModelClient(run_id=new_id("live"), settings=settings, http=proxy.client)
    try:
        for tier in TIERS:
            chat = await client.chat(
                tier=tier, purpose="agent", messages=_PROMPT, max_tokens=20, thinking=False
            )
            assert chat.tier == tier and chat.completion_tokens > 0, tier
            assert chat.usd > 0 and not chat.cached, tier
    finally:
        await proxy.aclose()
    totals = store.spend()
    assert all(totals.usd[t] > 0 for t in TIERS)  # one cost row per tier at least
    assert totals.total_usd < 0.05


async def test_tavily_searches_parse_into_drift_checks(tmp_path: Path) -> None:
    """Two live Tavily searches parse into DriftCheck rows (value, source, ref)."""
    settings = _live_settings(tmp_path)
    if settings.tavily_api_key is None:
        pytest.skip("TAVILY_API_KEY is not set")
    store = Store.open(settings.ratchet_data_dir)
    tavily = LiveTavily(settings.tavily_api_key.get_secret_value(), store)
    queries = ("EUR to USD exchange rate today", "Python latest stable release version")
    checks: list[DriftCheck] = []
    for i, query in enumerate(queries):
        results = await tavily.search(query, SearchParams(time_range="week"))
        assert results.hits, query
        top = results.hits[0]
        checks.append(
            DriftCheck(
                fact_id=f"f{i}",
                query=query,
                tavily_results_ref=results.ref,
                changed=False,
                old_value="",
                new_value=(results.answer or top.content)[:200],
                confidence=top.score,
                source_url=top.url,
                source_title=top.title,
            )
        )
    for check in checks:
        assert check.tavily_results_ref and check.source_url
        assert check.new_value and check.status == "checked"
        assert DriftCheck.model_validate_json(check.model_dump_json()) == check
