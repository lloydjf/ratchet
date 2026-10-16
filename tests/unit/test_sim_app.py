"""Simulator HTTP surface: models, tiers, usage, streaming, embeddings, determinism (ADR 0018)."""

from __future__ import annotations

import json
import math

import httpx
import pytest

from ratchet.judge.prompts import prompt_hash
from ratchet.proxy.stream import ChatStreamAssembler, SseParser
from ratchet.sim.app import create_app, resolve_tier
from ratchet.tools.catalog import openai_tools

AGENT = {"X-Ratchet-Purpose": "agent"}
SYSTEM = "Current date: 2026-10-14T16:00:00+00:00\n\nAvailable skills:\n- none: none"


def _body(task: str = "What is 17% of 240?", **extra: object) -> dict[str, object]:
    return {
        "model": "ratchet-sim/super",
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}],
        "temperature": 0,
        **extra,
    }


@pytest.fixture
async def client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://ratchet-sim") as c:
        yield c


async def test_models_and_health(client: httpx.AsyncClient) -> None:
    models = (await client.get("/v1/models")).json()
    ids = [m["id"] for m in models["data"]]
    assert ids == ["ratchet-sim/nano", "ratchet-sim/super", "ratchet-sim/ultra"]
    assert (await client.get("/healthz")).json() == {"ok": True, "simulated": True}


def test_tier_comes_from_the_model_id_or_the_header() -> None:
    assert resolve_tier("ratchet-sim/ultra", None) == "ultra"
    assert resolve_tier("nano", None) == "nano"
    assert resolve_tier("nvidia/some-model", "super") == "super"
    assert resolve_tier("nvidia/some-model", None) is None


async def test_unknown_model_is_404_and_bad_bodies_are_400(client: httpx.AsyncClient) -> None:
    unknown = await client.post("/v1/chat/completions", json=_body(model="gpt-x"))
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "model_not_found"
    bad = await client.post("/v1/chat/completions", content=b"not json")
    assert bad.status_code == 400
    empty = await client.post("/v1/chat/completions", json=_body(messages=[]))
    assert empty.status_code == 400


async def test_plain_completion_shape_and_model_label(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/chat/completions", json=_body(), headers=AGENT)
    payload = response.json()
    assert response.headers["x-ratchet-simulated"] == "1"
    assert payload["object"] == "chat.completion"
    assert payload["model"] == "ratchet-sim/super"
    assert payload["choices"][0]["message"] == {
        "role": "assistant",
        "content": "17% of 240 is 40.8.",
    }
    assert payload["choices"][0]["finish_reason"] == "stop"


async def test_header_tier_overrides_a_foreign_model_id(client: httpx.AsyncClient) -> None:
    headers = {**AGENT, "X-Ratchet-Tier": "nano"}
    response = await client.post("/v1/chat/completions", json=_body(model="x"), headers=headers)
    assert response.json()["model"] == "ratchet-sim/nano"


async def test_usage_counts_four_chars_per_token_including_tool_schemas(
    client: httpx.AsyncClient,
) -> None:
    bare = (await client.post("/v1/chat/completions", json=_body())).json()["usage"]
    tools = openai_tools()
    with_tools = (await client.post("/v1/chat/completions", json=_body(tools=tools))).json()
    schema_tokens = math.ceil(len(json.dumps(tools, sort_keys=True, separators=(",", ":"))) / 4)
    assert with_tools["usage"]["prompt_tokens"] == bare["prompt_tokens"] + schema_tokens
    assert schema_tokens > 500  # the full catalog is a realistic prompt cost
    assert bare["completion_tokens"] == math.ceil(len("17% of 240 is 40.8.") / 4)
    assert bare["total_tokens"] == bare["prompt_tokens"] + bare["completion_tokens"]


async def test_same_request_gives_identical_bytes(client: httpx.AsyncClient) -> None:
    body = _body(tools=openai_tools())
    first = await client.post("/v1/chat/completions", json=body)
    second = await client.post("/v1/chat/completions", json=body)
    assert first.content == second.content
    streamed = [
        (await client.post("/v1/chat/completions", json={**body, "stream": True})).content
        for _ in range(2)
    ]
    assert streamed[0] == streamed[1]


async def test_stream_reassembles_to_the_plain_answer_with_a_usage_chunk(
    client: httpx.AsyncClient,
) -> None:
    triage = _body("Triage the open issues in acme/widgets.")
    triage["messages"][0]["content"] = (  # type: ignore[index]
        f"{SYSTEM}\n\n## Skill: github-triage\nCall `github.list_issues` once."
    )
    plain = (await client.post("/v1/chat/completions", json=triage, headers=AGENT)).json()
    body = {**triage, "stream": True, "stream_options": {"include_usage": True}}
    response = await client.post("/v1/chat/completions", json=body, headers=AGENT)
    assert response.headers["content-type"].startswith("text/event-stream")
    parser, assembler = SseParser(), ChatStreamAssembler()
    events = parser.feed(response.content) + parser.close()
    assert events[-1] == "[DONE]"
    chunks = [json.loads(e) for e in events[:-1]]
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"] == plain["usage"]
    assert chunks[-2]["choices"][0]["finish_reason"] == "tool_calls"
    assembler.feed(response.content)
    assembler.close()
    rebuilt = assembler.response()
    message = rebuilt["choices"][0]["message"]
    assert message["tool_calls"] == plain["choices"][0]["message"]["tool_calls"]
    assert rebuilt["usage"] == plain["usage"]


async def test_stream_without_include_usage_has_no_usage_chunk(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/chat/completions", json=_body(stream=True))
    events = SseParser().feed(response.content)
    assert all("usage" not in json.loads(e) for e in events if e != "[DONE]")
    text = "".join(
        json.loads(e)["choices"][0]["delta"].get("content", "") for e in events if e != "[DONE]"
    )
    assert text == "17% of 240 is 40.8."


async def test_run_scoped_path_is_served(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/r/run_abc/chat/completions", json=_body(), headers=AGENT)
    assert response.json()["choices"][0]["message"]["content"] == "17% of 240 is 40.8."


async def test_max_tokens_truncates_with_finish_reason_length(client: httpx.AsyncClient) -> None:
    long_task = "Summarize this note: " + "word " * 200 + "end."
    response = await client.post("/v1/chat/completions", json=_body(long_task, max_tokens=5))
    payload = response.json()
    assert payload["choices"][0]["finish_reason"] == "length"
    assert len(payload["choices"][0]["message"]["content"]) == 20
    assert payload["usage"]["completion_tokens"] == 5


async def test_judge_call_with_thinking_carries_simulated_reasoning(
    client: httpx.AsyncClient,
) -> None:
    headers = {
        "X-Ratchet-Purpose": "adjudicate",
        "X-Ratchet-Prompt": f"adjudicate@{prompt_hash('adjudicate')[:12]}",
    }
    body = _body("Task input: x", chat_template_kwargs={"enable_thinking": True})
    payload = (await client.post("/v1/chat/completions", json=body, headers=headers)).json()
    message = payload["choices"][0]["message"]
    assert "No model was run" in message["reasoning_content"]
    assert payload["usage"]["completion_tokens_details"]["reasoning_tokens"] > 0
    json.loads(message["content"])  # still a JSON answer


async def test_unknown_prompt_answers_text_that_is_not_json(client: httpx.AsyncClient) -> None:
    headers = {"X-Ratchet-Purpose": "extract", "X-Ratchet-Prompt": "mystery@000000000000"}
    payload = (await client.post("/v1/chat/completions", json=_body(), headers=headers)).json()
    content = payload["choices"][0]["message"]["content"]
    with pytest.raises(ValueError):
        json.loads(content)


async def test_embeddings_are_deterministic_normalized_and_similarity_preserving(
    client: httpx.AsyncClient,
) -> None:
    texts = [
        "Triage the open issues in acme/widgets.",
        "Triage open issues in acme/widgets please.",
        "What is 17% of 240?",
    ]
    body = {"model": "Qwen/Qwen3-Embedding-8B", "input": texts, "dimensions": 64}
    first = (await client.post("/v1/embeddings", json=body)).json()
    second = (await client.post("/v1/embeddings", json=body)).json()
    assert first == second
    vectors = [d["embedding"] for d in first["data"]]
    assert all(len(v) == 64 for v in vectors)
    assert all(abs(sum(x * x for x in v) - 1.0) < 1e-4 for v in vectors)

    def cos(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert cos(vectors[0], vectors[1]) > cos(vectors[0], vectors[2])
    assert first["usage"]["prompt_tokens"] > 0
    bad = await client.post("/v1/embeddings", json={"input": [1, 2]})
    assert bad.status_code == 400
