"""Embeddings for trigger-neighborhood selection (vectors are cached).

Two embedders share one protocol:

* :class:`LocalHashEmbedder` — pure Python, offline, deterministic. Lowercased word tokens
  minus stopwords, light suffix stemming, unigrams + bigrams hashed (signed) into 512
  dimensions, sublinear term frequency times an IDF-like weight (stopwords 0, generic
  task words 0.35, bigrams 0.6), L2-normalized.
* :class:`TokenFactoryEmbedder` — ``Qwen/Qwen3-Embedding-8B`` (id from
  ``ratchet.config.models.embed_model``) through the capture proxy's ``/embeddings``.

Threshold calibration (LocalHashEmbedder, ``default_threshold = 0.12``): measured on the
fixture set in ``tests/unit/test_embed.py`` (4 SkillRegress-Bench skill descriptions, each
against 4 clearly related and 4 unrelated tasks). Related tasks scored 0.14-0.57;
unrelated tasks -0.20-0.09 (non-zero values are signed hash collisions). 0.12 sits in the
gap, nearer the unrelated side because a false neighbor costs one extra replay while a
missed neighbor can hide a trigger over-fire. A related task sharing only one
content word with the description can fall below it — the price of a bag-of-words model.
TokenFactoryEmbedder keeps the design threshold of 0.80, to be re-checked against live
Qwen3 scores. ``RATCHET_NEIGHBORHOOD_THRESHOLD`` overrides either.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import httpx

from ratchet.config.models import embed_model
from ratchet.config.settings import Settings, get_settings
from ratchet.errors import BudgetExceeded, UpstreamError
from ratchet.llm.client import HEADER
from ratchet.log import get_logger
from ratchet.store.base import write_json_atomic
from ratchet.util import sha256_hex

__all__ = [
    "Embedder",
    "LocalHashEmbedder",
    "TokenFactoryEmbedder",
    "EmbeddingCache",
    "CachedEmbedder",
    "cosine",
    "tokenize",
    "get_embedder",
]

_log = get_logger("selection")
_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str) -> frozenset[str]:
    return frozenset(text.split())


_STOPWORDS = _words(
    """a about above after again all also am an and any are as at be because been before
    being below between both but by can could did do does doing done down during each few
    for from further had has have having he her here hers him his how i if in into is it
    its itself just me more most my myself no nor not now of off on once only or other our
    ours out over own please same she should so some such than that the their theirs them
    then there these they this those through to too under until up very was we were what
    when where which while who whom why will with would you your yours yourself let lets
    im thanks thank hi hello ok okay"""
)
_GENERIC_WORDS = _words(
    """use using used make get got go need needs want task tasks thing things new help
    file files run way today one two sure via based given"""
)
_SUFFIXES = (
    ("ations", ""),
    ("ation", ""),
    ("ings", ""),
    ("ing", ""),
    ("ies", "y"),
    ("es", ""),
    ("ed", ""),
    ("s", ""),
    ("e", ""),
)
_GENERIC_WEIGHT = 0.35
_BIGRAM_WEIGHT = 0.6


def _stem(word: str) -> str:
    for suffix, replacement in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: len(word) - len(suffix)] + replacement
    return word


_GENERIC = frozenset(_stem(w) for w in _GENERIC_WORDS)


def tokenize(text: str) -> list[str]:
    """Content-word stems in order (stopwords and 1-char tokens dropped)."""
    words = _WORD.findall(text.lower())
    return [_stem(w) for w in words if len(w) > 1 and w not in _STOPWORDS]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na > 0 and nb > 0 else 0.0


class Embedder(Protocol):
    name: str
    default_threshold: float

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class LocalHashEmbedder:
    """Deterministic hashed bag of unigrams + bigrams; no network, no model."""

    default_threshold = 0.12

    def __init__(self, dims: int = 512) -> None:
        self.dims = dims
        self.name = f"localhash-{dims}"

    def _feature(self, vec: list[float], feature: str, weight: float) -> None:
        digest = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        sign = 1.0 if (digest >> 63) & 1 else -1.0
        vec[digest % self.dims] += sign * weight

    def vector(self, text: str) -> list[float]:
        tokens = tokenize(text)
        vec = [0.0] * self.dims
        unigrams = Counter(tokens)
        for token, tf in unigrams.items():
            idf = _GENERIC_WEIGHT if token in _GENERIC else 1.0
            self._feature(vec, token, (1.0 + math.log(tf)) * idf)
        bigrams = Counter(f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False))
        for gram, tf in bigrams.items():
            self._feature(vec, gram, (1.0 + math.log(tf)) * _BIGRAM_WEIGHT)
        norm = math.sqrt(sum(x * x for x in vec))
        return [x / norm for x in vec] if norm > 0 else vec

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]


class TokenFactoryEmbedder:
    """Token Factory ``/v1/embeddings`` via the capture proxy (``<proxy>/embeddings``)."""

    default_threshold = 0.80

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http: httpx.AsyncClient | None = None,
        run_id: str = "embed_selection",
        batch_size: int = 32,
        dimensions: int | None = None,
        timeout_s: float = 30.0,
        backoff_s: float = 2.0,
    ) -> None:
        self.settings = settings or get_settings()
        self.model = embed_model(self.settings)
        self.name = f"tokenfactory:{self.model}" + (f":{dimensions}" if dimensions else "")
        self._http = http
        self.run_id = run_id
        self.batch_size = batch_size
        self.dimensions = dimensions
        self.timeout_s = timeout_s
        self.backoff_s = backoff_s

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(await self._batch(list(texts[start : start + self.batch_size])))
        return vectors

    async def _batch(self, chunk: list[str]) -> list[list[float]]:
        body: dict[str, object] = {"model": self.model, "input": chunk, "encoding_format": "float"}
        if self.dimensions:
            body["dimensions"] = self.dimensions
        headers = {HEADER.RUN_ID: self.run_id, HEADER.PURPOSE: "embed"}
        url = self.settings.ratchet_proxy_url.rstrip("/") + "/embeddings"
        client = self._http or httpx.AsyncClient()
        try:
            for attempt in range(2):  # 1 retry on 5xx / timeout, 2 s backoff
                try:
                    response = await client.post(
                        url, json=body, headers=headers, timeout=self.timeout_s
                    )
                except httpx.HTTPError as exc:
                    if attempt == 0:
                        await asyncio.sleep(self.backoff_s)
                        continue
                    raise UpstreamError(f"embeddings unreachable: {type(exc).__name__}") from exc
                if response.status_code >= 500 and attempt == 0:
                    await asyncio.sleep(self.backoff_s)
                    continue
                return _parse_embeddings(response, len(chunk))
            raise UpstreamError("embeddings failed after retry")
        finally:
            if self._http is None:
                await client.aclose()


def _parse_embeddings(response: httpx.Response, expected: int) -> list[list[float]]:
    if response.status_code == 429:
        raise BudgetExceeded("spending cap reached", context={"purpose": "embed"})
    if response.status_code >= 400:
        raise UpstreamError(
            f"embeddings status {response.status_code}",
            retryable=response.status_code >= 500,
            context={"status": response.status_code},
        )
    try:
        data = response.json()["data"]
        rows = sorted(data, key=lambda row: int(row.get("index", 0)))
        vectors = [[float(x) for x in row["embedding"]] for row in rows]
    except (ValueError, KeyError, TypeError) as exc:
        raise UpstreamError("embeddings response unreadable", code="schema_failure") from exc
    if len(vectors) != expected:
        raise UpstreamError("embeddings response size mismatch", code="schema_failure")
    return vectors


class EmbeddingCache:
    """Vectors keyed by sha256(embedder name + text) in one JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, list[float]] | None = None

    @staticmethod
    def key(embedder: str, text: str) -> str:
        return sha256_hex(f"{embedder}\0{text}")

    def _loaded(self) -> dict[str, list[float]]:
        if self._data is None:
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._data = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                self._data = {}
        return self._data

    def get(self, embedder: str, text: str) -> list[float] | None:
        with self._lock:
            return self._loaded().get(self.key(embedder, text))

    def put_many(self, embedder: str, items: dict[str, list[float]]) -> None:
        with self._lock:
            data = self._loaded()
            for text, vector in items.items():
                data[self.key(embedder, text)] = [round(x, 6) for x in vector]
            write_json_atomic(self.path, data)


class CachedEmbedder:
    """Wraps an embedder; only texts missing from the cache are sent."""

    def __init__(self, inner: Embedder, cache: EmbeddingCache) -> None:
        self.inner = inner
        self.cache = cache
        self.name = inner.name
        self.default_threshold = inner.default_threshold

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        found = {text: self.cache.get(self.name, text) for text in set(texts)}
        missing = sorted(text for text, vector in found.items() if vector is None)
        if missing:
            fresh = dict(zip(missing, await self.inner.embed(missing), strict=True))
            await asyncio.to_thread(self.cache.put_many, self.name, fresh)
            found.update(fresh)
        return [list(found[text] or []) for text in texts]


def get_embedder(
    settings: Settings | None = None, *, http: httpx.AsyncClient | None = None
) -> Embedder:
    """The embedder named by ``ratchet_selection_embedder``; Token Factory is cached under
    ``<data_dir>/embeddings/cache.json``."""
    settings = settings or get_settings()
    if settings.ratchet_selection_embedder == "tokenfactory":
        cache = EmbeddingCache(settings.ratchet_data_dir / "embeddings" / "cache.json")
        return CachedEmbedder(TokenFactoryEmbedder(settings, http=http), cache)
    return LocalHashEmbedder()
