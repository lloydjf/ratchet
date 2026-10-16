"""A scripted fake Token Factory for proxy tests (no network; httpx.MockTransport).

Tests have no package ``__init__``, so test modules import this file by path (see
``_load_fake`` in ``tests/integration/test_proxy_app.py``).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from fastapi import FastAPI
from pydantic import SecretStr

from ratchet.config.settings import Settings, configure
from ratchet.proxy.app import create_app
from ratchet.store import Store

Reply = Callable[[httpx.Request], httpx.Response]

FAKE_KEY = "tf-live-key-DO-NOT-STORE-9a8b7c6d5e4f"


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def chat_json(
    content: str = "ok",
    *,
    usage: dict[str, int] | None = None,
    tool_calls: list[dict[str, object]] | None = None,
    no_usage: bool = False,
) -> Reply:
    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        message: dict[str, object] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        payload: dict[str, object] = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": body.get("model"),
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        }
        if not no_usage:
            payload["usage"] = usage or {"prompt_tokens": 1000, "completion_tokens": 200}
        return httpx.Response(200, json=payload, headers={"x-ratelimit-remaining-requests": "59"})

    return reply


def sse_chunks(texts: list[str], *, usage: dict[str, int] | None = None) -> list[bytes]:
    chunks = []
    for i, text in enumerate(texts):
        delta: dict[str, object] = {"content": text}
        if i == 0:
            delta["role"] = "assistant"
        event = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "model": "m",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        chunks.append(f"data: {json.dumps(event)}\n\n".encode())
    if usage is not None:
        tail = {"id": "c1", "object": "chat.completion.chunk", "choices": [], "usage": usage}
        chunks.append(f"data: {json.dumps(tail)}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return chunks


def sse(chunks: list[bytes]) -> Reply:
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=_Chunks(list(chunks))
        )

    return reply


def status(code: int, body: dict[str, object] | None = None) -> Reply:
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(code, json=body or {"error": {"message": f"status {code}"}})

    return reply


def timeout() -> Reply:
    def reply(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("upstream too slow", request=request)

    return reply


class FakeUpstream:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.queue: list[Reply] = []
        self.default: Reply = chat_json()

    def push(self, *replies: Reply) -> None:
        self.queue.extend(replies)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.queue.pop(0) if self.queue else self.default
        return reply(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def bodies(self) -> list[dict[str, object]]:
        return [json.loads(r.content) for r in self.requests]


@dataclass
class Env:
    settings: Settings
    store: Store
    fake: FakeUpstream
    app: FastAPI
    data_dir: Path

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://proxy"
        )


def make_env(tmp_path: Path, **overrides: object) -> Env:
    fields: dict[str, object] = {
        "ratchet_upstream": "live",
        "nebius_token_factory_key": SecretStr(FAKE_KEY),
        "ratchet_data_dir": tmp_path / "data",
    }
    fields.update(overrides)
    settings = configure(Settings(_env_file=None, **fields))  # type: ignore[arg-type]
    store = Store.open(settings.ratchet_data_dir)
    fake = FakeUpstream()
    app = create_app(settings, store, fake.transport, retry_backoff_s=0.0)
    return Env(settings, store, fake, app, settings.ratchet_data_dir)
