"""Embedders: local hash calibration, Token Factory via the proxy (respx), the vector cache."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
import respx

from ratchet.config.settings import Settings
from ratchet.errors import BudgetExceeded, UpstreamError
from ratchet.orchestrator.embed import (
    CachedEmbedder,
    EmbeddingCache,
    LocalHashEmbedder,
    TokenFactoryEmbedder,
    cosine,
    get_embedder,
    tokenize,
)

# Calibration fixtures (documented in ratchet/orchestrator/embed.py): bench skills,
# each with 4 clearly related and 4 unrelated tasks.
CALIBRATION: dict[str, tuple[str, list[str], list[str]]] = {
    "github-triage": (
        "Triage GitHub issues in a repository: label each open issue by area, comment on "
        "duplicates and close issues that are obviously duplicates.",
        [
            "Triage the open issues in acme/widgets and label them",
            "Label the new GitHub issues on the widgets repo by area",
            "Find duplicate issues in acme/api and close them",
            "Go through the issue backlog and comment on duplicates",
        ],
        [
            "Book a meeting with Dana next Tuesday afternoon",
            "Convert my Paris receipts from EUR to USD",
            "Clean up the CSV export of customers",
            "Summarize this week's AI research news",
        ],
    ),
    "expense-report": (
        "Build an expense report from receipt photos or PDFs: parse each receipt, append the "
        "amount, date, merchant and VAT line to the expenses sheet, and email the report.",
        [
            "Make an expense report from the receipts in my inbox",
            "Parse these receipts and add them to the expenses sheet",
            "Email my September expense report with the VAT totals",
            "Add the hotel receipt to my expense sheet",
        ],
        [
            "Triage the open issues in acme/widgets",
            "Write the changelog for release 2.3",
            "Schedule a call with the design team",
            "Update the outdated npm dependencies",
        ],
    ),
    "calendar-scheduler": (
        "Schedule meetings: check free/busy on the calendar, propose a time, create the "
        "calendar event and email the invite to attendees.",
        [
            "Schedule a 30 minute meeting with Dana next week",
            "Find a free slot on my calendar Thursday and book a call with Sam",
            "Create a calendar event for the team retro and email the invite",
            "Book a meeting with the design team for Friday",
        ],
        [
            "Parse these receipts into the sheet",
            "Close duplicate GitHub issues",
            "Clean the customers CSV file",
            "Write release notes from the git log",
        ],
    ),
    "changelog-writer": (
        "Write a changelog for a release from the git log: group commits into features, "
        "fixes and breaking changes and write CHANGELOG.md.",
        [
            "Write the changelog for v2.3 from the git log",
            "Update CHANGELOG.md with the commits since the last release",
            "Draft release notes grouping fixes and features",
            "Summarize the commits since v1.0 into a changelog",
        ],
        [
            "Book a meeting with Dana",
            "Convert EUR receipts to USD",
            "Label the open GitHub issues",
            "Email the weekly meeting notes",
        ],
    ),
}


@pytest.mark.parametrize("skill", sorted(CALIBRATION))
async def test_local_hash_threshold_separates_related_from_unrelated(skill: str) -> None:
    embedder = LocalHashEmbedder()
    description, related, unrelated = CALIBRATION[skill]
    desc, *related_vecs = await embedder.embed([description, *related])
    unrelated_vecs = await embedder.embed(unrelated)
    threshold = embedder.default_threshold
    assert all(cosine(desc, v) >= threshold for v in related_vecs)
    assert all(cosine(desc, v) < threshold for v in unrelated_vecs)


def test_local_hash_vectors_are_deterministic_and_normalized() -> None:
    embedder = LocalHashEmbedder()
    a = embedder.vector("Triage the GitHub issues")
    assert a == LocalHashEmbedder().vector("Triage the GitHub issues")
    assert len(a) == 512
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)
    assert embedder.vector("the and of") == [0.0] * 512
    assert cosine(a, embedder.vector("the and of")) == 0.0


def test_tokenize_stems_and_drops_stopwords() -> None:
    assert tokenize("Please triage the issues") == tokenize("triaging issue")
    assert tokenize("Closed duplicates; labels!") == ["clos", "duplicat", "label"]
    assert tokenize("replies") == tokenize("reply")


def test_cosine_basics() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def settings_for(tmp_path: Path, **extra: object) -> Settings:
    return Settings(
        ratchet_proxy_url="http://proxy.test/v1",
        ratchet_data_dir=tmp_path / "data",
        **extra,  # type: ignore[arg-type]
    )


def embeddings_response(vectors: Sequence[Sequence[float]]) -> httpx.Response:
    data = [
        {"index": i, "embedding": list(v), "object": "embedding"} for i, v in enumerate(vectors)
    ]
    return httpx.Response(200, json={"data": list(reversed(data)), "model": "m"})


@respx.mock
async def test_token_factory_posts_to_the_proxy_with_purpose_embed(tmp_path: Path) -> None:
    route = respx.post("http://proxy.test/v1/embeddings").mock(
        return_value=embeddings_response([[1.0, 0.0], [0.0, 1.0]])
    )
    embedder = TokenFactoryEmbedder(settings_for(tmp_path), backoff_s=0)
    vectors = await embedder.embed(["a", "b"])
    assert vectors == [[1.0, 0.0], [0.0, 1.0]]  # re-ordered by index
    request = route.calls.last.request
    body = json.loads(request.content)
    assert body["input"] == ["a", "b"]
    assert body["model"] == embedder.model and "Embedding" in embedder.model
    assert request.headers["X-Ratchet-Purpose"] == "embed"
    assert request.headers["X-Ratchet-Run-Id"] == "embed_selection"
    assert embedder.default_threshold == 0.80


@respx.mock
async def test_token_factory_batches_and_retries_once_on_5xx(tmp_path: Path) -> None:
    route = respx.post("http://proxy.test/v1/embeddings").mock(
        side_effect=[
            httpx.Response(503),
            embeddings_response([[1.0], [2.0]]),
            embeddings_response([[3.0]]),
        ]
    )
    embedder = TokenFactoryEmbedder(settings_for(tmp_path), batch_size=2, backoff_s=0)
    assert await embedder.embed(["a", "b", "c"]) == [[1.0], [2.0], [3.0]]
    assert route.call_count == 3


@respx.mock
async def test_token_factory_errors_map_to_ratchet_errors(tmp_path: Path) -> None:
    respx.post("http://proxy.test/v1/embeddings").mock(return_value=httpx.Response(502))
    embedder = TokenFactoryEmbedder(settings_for(tmp_path), backoff_s=0)
    with pytest.raises(UpstreamError):
        await embedder.embed(["a"])
    respx.post("http://proxy.test/v1/embeddings").mock(return_value=httpx.Response(429))
    with pytest.raises(BudgetExceeded):
        await embedder.embed(["a"])
    respx.post("http://proxy.test/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    with pytest.raises(UpstreamError) as info:
        await embedder.embed(["a"])
    assert info.value.code == "schema_failure"
    respx.post("http://proxy.test/v1/embeddings").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(UpstreamError):
        await embedder.embed(["a"])


class CountingEmbedder:
    name = "counting"
    default_threshold = 0.3

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


async def test_cached_embedder_only_embeds_misses_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "embeddings" / "cache.json"
    inner = CountingEmbedder()
    cached = CachedEmbedder(inner, EmbeddingCache(path))
    assert cached.default_threshold == 0.3
    assert await cached.embed(["aa", "b", "aa"]) == [[2.0, 1.0], [1.0, 1.0], [2.0, 1.0]]
    assert await cached.embed(["b", "ccc"]) == [[1.0, 1.0], [3.0, 1.0]]
    assert inner.calls == [["aa", "b"], ["ccc"]]
    assert path.exists()

    fresh_inner = CountingEmbedder()
    reloaded = CachedEmbedder(fresh_inner, EmbeddingCache(path))
    assert await reloaded.embed(["aa", "ccc"]) == [[2.0, 1.0], [3.0, 1.0]]
    assert fresh_inner.calls == []
    assert all(len(key) == 64 for key in json.loads(path.read_text()))  # hashes, not text


def test_get_embedder_follows_settings(tmp_path: Path) -> None:
    assert isinstance(get_embedder(settings_for(tmp_path)), LocalHashEmbedder)
    remote = get_embedder(settings_for(tmp_path, ratchet_selection_embedder="tokenfactory"))
    assert isinstance(remote, CachedEmbedder)
    assert remote.default_threshold == 0.80
