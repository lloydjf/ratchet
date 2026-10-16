"""``ratchet verify-live``: live model and Tavily checks with real keys (about $0.05),
results appended to ``docs/VERIFIED.md`` under "Live results".

Checks: the model ids in the catalog (``GET /models``), ``seed`` repeatability (5 identical
calls on Super), ``json_schema`` structured output per tier, the thinking toggle on Ultra,
``stream_options.include_usage`` on a streamed call, and Tavily ``time_range``. Every model
call goes through an in-process capture proxy, so it is costed like any other call. Agent
purpose is used for the repeatability calls because judge purposes are response-cached.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx

from ratchet import clock
from ratchet.config.models import spec_for
from ratchet.config.settings import Settings
from ratchet.errors import RatchetError
from ratchet.judge.tavily import LiveTavily, SearchParams
from ratchet.llm.client import HEADER, ModelClient
from ratchet.models import TIERS, Tier
from ratchet.orchestrator.context import InProcessProxy
from ratchet.store import Store
from ratchet.util import new_id

__all__ = ["LiveResult", "ESTIMATE_USD", "missing_keys", "run_checks", "append_results"]

ESTIMATE_USD = 0.05
_PROMPT: list[dict[str, object]] = [
    {"role": "user", "content": "Reply with the single word: ready"}
]
_SCHEMA: dict[str, object] = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer",
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
}


@dataclass(frozen=True)
class LiveResult:
    check: str
    ok: bool
    detail: str


def missing_keys(settings: Settings) -> list[str]:
    missing = []
    if settings.nebius_token_factory_key is None:
        missing.append("NEBIUS_TOKEN_FACTORY_KEY")
    if settings.tavily_api_key is None:
        missing.append("TAVILY_API_KEY")
    return missing


async def _catalog(settings: Settings) -> LiveResult:
    key = settings.require_live_key().get_secret_value()
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(
            settings.nebius_tf_base_url + "models", headers={"Authorization": f"Bearer {key}"}
        )
    ids = {str(m.get("id")) for m in response.json().get("data", [])}
    missing = [t for t in TIERS if spec_for(t, settings).model_id not in ids]
    return LiveResult("model ids", not missing, f"missing: {missing}" if missing else "all found")


async def _seed(client: ModelClient) -> LiveResult:
    texts = []
    for _ in range(5):
        chat = await client.chat(
            tier="super", purpose="agent", messages=_PROMPT, max_tokens=16, seed=7
        )
        texts.append(chat.text.strip())
    same = len(set(texts))
    return LiveResult("seed repeatability (super, 5 calls)", same == 1, f"{same} distinct outputs")


async def _schema(client: ModelClient, tier: Tier) -> LiveResult:
    try:
        chat = await client.chat(
            tier=tier, purpose="agent", messages=_PROMPT, max_tokens=64, response_format=_SCHEMA
        )
        json.loads(chat.text)
        return LiveResult(f"json_schema ({tier})", True, "valid JSON")
    except (RatchetError, ValueError) as exc:
        return LiveResult(f"json_schema ({tier})", False, type(exc).__name__)


async def _thinking(client: ModelClient) -> LiveResult:
    on = await client.chat(
        tier="ultra", purpose="agent", messages=_PROMPT, max_tokens=512, thinking=True
    )
    off = await client.chat(
        tier="ultra", purpose="agent", messages=_PROMPT, max_tokens=64, thinking=False
    )
    detail = f"reasoning on={bool(on.reasoning)} off={bool(off.reasoning)}"
    return LiveResult("thinking toggle (ultra)", bool(on.reasoning) and not off.reasoning, detail)


async def _usage(http: httpx.AsyncClient, settings: Settings) -> LiveResult:
    body = {
        "model": "nano",
        "messages": _PROMPT,
        "max_tokens": 16,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    headers = {HEADER.RUN_ID: new_id("verify"), HEADER.TIER: "nano", HEADER.PURPOSE: "agent"}
    url = settings.ratchet_proxy_url.rstrip("/") + "/chat/completions"
    async with http.stream("POST", url, json=body, headers=headers) as response:
        text = "".join([chunk async for chunk in response.aiter_text()])
    return LiveResult("include_usage (stream)", '"usage"' in text, "usage chunk present")


async def _tavily(settings: Settings, store: Store) -> LiveResult:
    if settings.tavily_api_key is None:
        return LiveResult("tavily time_range", False, "TAVILY_API_KEY not set")
    tavily = LiveTavily(settings.tavily_api_key.get_secret_value(), store)
    results = await tavily.search("EUR to USD exchange rate today", SearchParams(time_range="week"))
    return LiveResult("tavily time_range=week", bool(results.hits), f"{len(results.hits)} hits")


async def run_checks(settings: Settings, store: Store) -> list[LiveResult]:
    live = settings.model_copy(update={"ratchet_upstream": "live"})
    proxy = InProcessProxy(live, store)
    client = ModelClient(run_id=new_id("verify"), settings=live, http=proxy.client)
    results: list[LiveResult] = []
    try:
        results.append(await _catalog(live))
        results.append(await _seed(client))
        for tier in TIERS:
            results.append(await _schema(client, tier))
        results.append(await _thinking(client))
        results.append(await _usage(proxy.client, live))
        results.append(await _tavily(live, store))
    except RatchetError as exc:
        results.append(LiveResult("aborted", False, exc.code))
    finally:
        await proxy.aclose()
    return results


def append_results(path: Path, results: list[LiveResult], usd: float) -> None:
    stamp = clock.iso(clock.now())
    lines = [
        f"\n### {stamp} (spent ${usd:.4f})\n",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    lines += [f"| {r.check} | {'ok' if r.ok else 'FAIL'} | {r.detail} |" for r in results]
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
