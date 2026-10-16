"""POST /v1/embeddings passthrough: model rewrite, cost row under the embed pseudo-tier."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from ratchet.config.models import embed_model
from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.proxy.app import create_app
from ratchet.store import Store


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    s = configure(
        Settings(
            ratchet_data_dir=tmp_path / "data",
            ratchet_upstream="live",
            nebius_token_factory_key=SecretStr("tf-secret-value"),
        )
    )
    yield s
    reset_settings()


async def test_embeddings_forwards_embed_model_and_records_cost(settings: Settings) -> None:
    seen: dict[str, object] = {}

    def upstream(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [{"embedding": [0.1, 0.2], "index": 0}],
                "usage": {"prompt_tokens": 1000},
            },
        )

    store = Store.open(settings.ratchet_data_dir)
    app = create_app(settings, store, httpx.MockTransport(upstream), retry_backoff_s=0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        response = await client.post(
            "/v1/embeddings",
            json={"model": "anything", "input": ["triage issues"]},
            headers={"X-Ratchet-Run-Id": "embed_selection"},
        )
    assert response.status_code == 200
    assert response.json()["data"][0]["embedding"] == [0.1, 0.2]
    assert str(seen["path"]).endswith("/embeddings")
    assert seen["auth"] == "Bearer tf-secret-value"
    assert seen["body"] == {
        "model": embed_model(settings),
        "input": ["triage issues"],
        "encoding_format": "float",
    }
    by_purpose = store.spend_by_purpose()
    assert by_purpose["embed"]["embed"] == pytest.approx(1000 * 0.01 / 1_000_000)
    assert store.spend().total_usd == 0.0  # pseudo-tier stays out of nano/super/ultra totals


async def test_embeddings_rejects_body_without_input(settings: Settings) -> None:
    store = Store.open(settings.ratchet_data_dir)
    app = create_app(settings, store, httpx.MockTransport(lambda r: httpx.Response(500)))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        response = await client.post("/v1/embeddings", json={"model": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
