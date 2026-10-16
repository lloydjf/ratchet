"""Tavily sources for the drift check: live REST and recorded fixtures.

``LiveTavily`` calls ``POST https://api.tavily.com/search`` / ``/extract`` with httpx
(the same wire calls ``tavily-python`` 0.8.5 makes; httpx keeps it respx-testable and
async-native). 15 s timeout, two retries after 1 s and 3 s on timeouts, 429 and 5xx. Results
are cached in ``tavily_cache`` by the request (query plus filters) and the UTC day,
and the raw JSON is saved under ``<data>/tavily/<hash>.json`` — that relative path
is the ``DriftCheck.tavily_results_ref``. Only the judge on the control plane calls Tavily
(a trust boundary); the key is never logged.

``FixtureTavily(dir)`` serves recorded responses: ``<dir>/<sha256(query)[:16]>.json`` or an
``index.json`` mapping query → file name (or, failing an exact key, the one key made of the
same identifying words: filler words and dates ignored). A missing fixture raises
``UpstreamError(code="drift_unavailable")`` exactly like a Tavily outage.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol

import httpx

from ratchet import clock
from ratchet.errors import UpstreamError
from ratchet.log import get_logger
from ratchet.util import canonical_json, sha256_hex

__all__ = [
    "TavilyHit",
    "TavilyResults",
    "SearchParams",
    "TavilySource",
    "TavilyCache",
    "LiveTavily",
    "FixtureTavily",
    "fixture_name",
    "parse_results",
]

TAVILY_API = "https://api.tavily.com"
_TIMEOUT_S = 15.0
_BACKOFF_S = (1.0, 3.0)
_log = get_logger("judge.tavily")


@dataclass(frozen=True)
class TavilyHit:
    title: str
    url: str
    content: str
    score: float = 0.0
    published_date: str | None = None


@dataclass(frozen=True)
class TavilyResults:
    query: str
    hits: list[TavilyHit]
    ref: str  # where the raw response lives (DriftCheck.tavily_results_ref)
    answer: str | None = (
        None  # Tavily's synthesized answer (include_answer); cleaner than page text
    )


@dataclass(frozen=True)
class SearchParams:
    search_depth: Literal["basic", "advanced"] = "advanced"
    time_range: Literal["day", "week", "month", "year"] | None = "week"
    start_date: str | None = None  # YYYY-MM-DD; used instead of time_range for older claims
    include_domains: list[str] = field(default_factory=list)
    topic: Literal["general", "news", "finance"] = "general"
    max_results: int = 5

    def body(self, query: str) -> dict[str, object]:
        body: dict[str, object] = {
            "query": query,
            "search_depth": self.search_depth,
            "topic": self.topic,
            "max_results": self.max_results,
            # In live runs, quote pages' `content` is mostly navigation text, while Tavily's answer
            # states the value plainly ("1 EUR = 1.1196 USD"), so the answer is requested too.
            "include_answer": "basic",
        }
        if self.start_date:
            body["start_date"] = self.start_date
        elif self.time_range:
            body["time_range"] = self.time_range
        if self.include_domains:
            body["include_domains"] = list(self.include_domains)
        return body


class TavilySource(Protocol):
    async def search(self, query: str, params: SearchParams | None = None) -> TavilyResults: ...

    async def extract(self, url: str) -> str: ...


class TavilyCache(Protocol):
    @property
    def data_dir(self) -> Path: ...

    def cache_get(self, table: str, key: str, *, ttl: timedelta | None = None) -> object | None: ...

    def cache_put(self, table: str, key: str, value: object, *, model: str = "") -> None: ...

    def cache_count_since(self, table: str, since: datetime) -> int: ...


def parse_results(query: str, payload: object, ref: str) -> TavilyResults:
    results = payload.get("results") if isinstance(payload, dict) else None
    hits: list[TavilyHit] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        hits.append(
            TavilyHit(
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                content=str(item.get("content") or ""),
                score=float(item.get("score") or 0.0),
                published_date=str(item["published_date"]) if item.get("published_date") else None,
            )
        )
    answer = payload.get("answer") if isinstance(payload, dict) else None
    return TavilyResults(
        query=query, hits=hits, ref=ref, answer=str(answer) if isinstance(answer, str) else None
    )


def fixture_name(query: str) -> str:
    return f"{sha256_hex(query)[:16]}.json"


_FILLER = frozenset(
    {"a", "an", "the", "to", "of", "for", "in", "on", "vs", "today", "now", "current", "latest"}
)


def _terms(query: str) -> frozenset[str]:
    """The words that identify a query: lower-cased, filler and dates removed."""
    words = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", query.lower())
    return frozenset(w for w in words if w not in _FILLER and not re.fullmatch(r"\d{4}|\d{1,2}", w))


def _same_terms(query: str, mapping: dict[str, object]) -> str | None:
    """The index entry whose identifying words equal the query's, if exactly one key set does
    (the judge words a query from an observed fact differently from the recorded hint:
    "EUR to USD exchange rate today" vs "EUR USD exchange rate today")."""
    wanted = _terms(query)
    if not wanted:
        return None
    hits = [
        (key, str(name)) for key, name in mapping.items() if key != "*" and _terms(key) == wanted
    ]
    names = {name for _, name in hits}
    if len(names) > 1:
        # Several recordings of the same fact (one per date): the undated key is the latest.
        names = {name for key, name in hits if not re.search(r"\d{4}-\d{2}-\d{2}", key)}
    return names.pop() if len(names) == 1 else None


class LiveTavily:
    def __init__(
        self,
        api_key: str,
        store: TavilyCache | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        base_url: str = TAVILY_API,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        daily_cap: int | None = None,
    ) -> None:
        self._key = api_key
        self._store = store
        self._http = http
        self._daily_cap = daily_cap  # live searches per UTC day; None = unbounded
        self._base = base_url.rstrip("/")
        self._sleep = sleep

    async def _post(self, path: str, body: dict[str, object]) -> object:
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        client = self._http or httpx.AsyncClient(timeout=_TIMEOUT_S)
        try:
            for attempt in range(len(_BACKOFF_S) + 1):
                try:
                    response = await client.post(
                        self._base + path, json=body, headers=headers, timeout=_TIMEOUT_S
                    )
                except httpx.HTTPError as exc:
                    error = f"{type(exc).__name__}"
                else:
                    if response.status_code < 400:
                        payload: object = response.json()
                        return payload
                    error = f"status {response.status_code}"
                    if response.status_code != 429 and response.status_code < 500:
                        break
                if attempt < len(_BACKOFF_S):
                    await self._sleep(_BACKOFF_S[attempt])
        finally:
            if self._http is None:
                await client.aclose()
        _log.warning("tavily.failed", path=path, error=error)
        raise UpstreamError(f"tavily {path} failed: {error}", code="drift_unavailable")

    def _cache_key(self, query: str, params: SearchParams) -> str:
        day = clock.now().date().isoformat()
        return sha256_hex(canonical_json({"body": params.body(query), "day": day}))

    async def search(self, query: str, params: SearchParams | None = None) -> TavilyResults:
        params = params or SearchParams()
        key = self._cache_key(query, params)
        ref = f"tavily/{key[:24]}.json"
        if self._store is not None:
            cached = await asyncio.to_thread(self._store.cache_get, "tavily_cache", key)
            if cached is not None:
                return parse_results(query, cached, ref)
            if self._daily_cap is not None:
                day = clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
                used = await asyncio.to_thread(self._store.cache_count_since, "tavily_cache", day)
                if used >= self._daily_cap:
                    _log.warning("tavily.daily_cap", used=used, cap=self._daily_cap)
                    raise UpstreamError("tavily daily search cap reached", code="drift_unavailable")
        payload = await self._post("/search", params.body(query))
        if self._store is not None:
            await asyncio.to_thread(self._save, key, ref, payload)
        return parse_results(query, payload, ref)

    def _save(self, key: str, ref: str, payload: object) -> None:
        assert self._store is not None
        path = self._store.data_dir / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        self._store.cache_put("tavily_cache", key, payload)

    async def extract(self, url: str) -> str:
        payload = await self._post("/extract", {"urls": [url], "extract_depth": "basic"})
        results = payload.get("results") if isinstance(payload, dict) else None
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return str(results[0].get("raw_content") or "")
        return ""


class FixtureTavily:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, query: str) -> Path:
        index = self.root / "index.json"
        if index.exists():
            mapping = json.loads(index.read_text(encoding="utf-8"))
            if isinstance(mapping, dict) and query in mapping:
                return self.root / Path(str(mapping[query])).name
            exact = self.root / fixture_name(query)
            if isinstance(mapping, dict) and not exact.exists():
                if "*" in mapping:
                    # A fixture set about one fact (a scenario) answers any wording of the query.
                    return self.root / Path(str(mapping["*"])).name
                nearest = _same_terms(query, mapping)
                if nearest is not None:
                    return self.root / Path(nearest).name
        return self.root / fixture_name(query)

    async def search(self, query: str, params: SearchParams | None = None) -> TavilyResults:
        path = self._path(query)
        if not path.exists():
            raise UpstreamError("no recorded Tavily response", code="drift_unavailable")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("error"):
            raise UpstreamError("recorded Tavily failure", code="drift_unavailable")
        return parse_results(query, payload, f"fixture:{path.name}")

    async def extract(self, url: str) -> str:
        return ""
