"""Proxy integration against a fake Token Factory: capture, model ids, no key leaks."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from ratchet.config.models import DEFAULT_MODELS, model_for
from ratchet.config.pricing import cost_usd
from ratchet.config.settings import reset_settings
from ratchet.models import LLMCall


def _load_fake() -> ModuleType:
    name = "ratchet_test_fake_upstream"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "fixtures" / "proxy" / "fake_upstream.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


fu = _load_fake()
CHAT = "/v1/chat/completions"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_settings()


def _steps(env: object, run_id: str) -> list[LLMCall]:
    steps = env.store.collect_steps(run_id)  # type: ignore[attr-defined]
    return [s for s in steps if isinstance(s, LLMCall)]


async def test_plain_call_is_forwarded_recorded_and_tagged(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    headers = {
        "X-Ratchet-Run-Id": "run_plain",
        "X-Ratchet-Tier": "super",
        "X-Ratchet-Purpose": "agent",
        "X-Ratchet-Edit-Id": "edit_1",
        "X-Ratchet-Session": "sess_1",
        "X-Request-Id": "req_abc",
        "Authorization": "Bearer dummy-harness-key",
    }
    body = {"model": "whatever", "messages": [{"role": "user", "content": "hi"}]}
    async with env.client() as client:
        res = await client.post(CHAT, json=body, headers=headers)
    assert res.status_code == 200
    expected_usd = cost_usd("super", 1000, 200)
    assert res.headers["X-Ratchet-Run-Id"] == "run_plain"
    assert res.headers["X-Ratchet-Step-Id"] == "m001"
    assert res.headers["X-Ratchet-Model"] == model_for("super", env.settings)
    assert res.headers["X-Ratchet-Cached"] == "0"
    assert res.headers["X-Ratchet-Usage-Estimated"] == "0"
    assert float(res.headers["X-Ratchet-Usd"]) == pytest.approx(expected_usd)
    assert res.headers["X-Request-Id"] == "req_abc"
    assert res.headers["x-ratelimit-remaining-requests"] == "59"

    sent = env.fake.requests[0]
    assert sent.headers["authorization"] == f"Bearer {fu.FAKE_KEY}"
    assert not any(k.lower().startswith("x-ratchet") for k in sent.headers)
    assert str(sent.url) == env.settings.nebius_tf_base_url + "chat/completions"

    (step,) = _steps(env, "run_plain")
    assert (step.tier, step.purpose, step.api, step.prompt_tokens) == (
        "super",
        "agent",
        "chat",
        1000,
    )
    assert step.usd == pytest.approx(expected_usd) and step.content_preview == "ok"
    trace_dir = env.store.trace_dir("run_plain")
    assert json.loads((trace_dir / step.messages_ref).read_text())["model"] == step.model
    assert (trace_dir / step.response_ref).exists()
    (cost,) = env.store.query("SELECT * FROM costs")
    assert (cost["edit_id"], cost["session_hash"], cost["cached"]) == ("edit_1", "sess_1", 0)


async def test_streaming_passthrough_matches_upstream_and_records_usage(tmp_path: Path) -> None:
    """Streamed chunks reach the caller unchanged and the usage is recorded."""
    env = fu.make_env(tmp_path)
    chunks = fu.sse_chunks(
        ["Hel", "lo", ", wor", "ld"], usage={"prompt_tokens": 9, "completion_tokens": 4}
    )
    env.fake.push(fu.sse(chunks))
    headers = {"X-Ratchet-Run-Id": "run_stream", "X-Ratchet-Tier": "nano"}
    body = {"messages": [{"role": "user", "content": "hi"}], "stream": True}
    received: list[bytes] = []
    async with (
        env.client() as client,
        client.stream("POST", CHAT, json=body, headers=headers) as res,
    ):
        assert res.status_code == 200
        assert res.headers["content-type"].startswith("text/event-stream")
        assert res.headers["X-Ratchet-Step-Id"] == "m001"
        assert res.headers["X-Ratchet-Model"] == model_for("nano", env.settings)
        async for part in res.aiter_bytes():
            received.append(part)
    assert b"".join(received) == b"".join(chunks)
    assert env.fake.bodies()[0]["stream_options"] == {"include_usage": True}

    (step,) = _steps(env, "run_stream")
    assert (step.prompt_tokens, step.completion_tokens, step.usage_estimated) == (9, 4, False)
    assert step.usd == pytest.approx(cost_usd("nano", 9, 4))
    stored = json.loads((env.store.trace_dir("run_stream") / step.response_ref).read_text())
    assert stored["object"] == "chat.completion"
    assert stored["choices"][0]["message"]["content"] == "Hello, world"


async def test_stream_without_usage_is_estimated(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    env.fake.push(fu.sse(fu.sse_chunks(["abcd" * 10])))
    body = {"messages": [{"role": "user", "content": "x"}], "stream": True}
    async with env.client() as client:
        res = await client.post(CHAT, json=body, headers={"X-Ratchet-Run-Id": "run_est"})
    assert res.status_code == 200
    (step,) = _steps(env, "run_est")
    assert step.usage_estimated and step.completion_tokens == 10


@pytest.mark.parametrize("tier", ["nano", "super", "ultra"])
async def test_tier_header_reaches_upstream_as_configured_model_id(
    tmp_path: Path, tier: str
) -> None:
    """The model id comes from ratchet.config.models only."""
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        await client.post(
            CHAT, json={"model": "gpt-4o", "messages": []}, headers={"X-Ratchet-Tier": tier}
        )
    assert env.fake.bodies()[0]["model"] == DEFAULT_MODELS[tier].model_id  # type: ignore[index]


async def test_model_tier_literal_and_agent_tier_default(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_agent_tier="nano")
    async with env.client() as client:
        await client.post(CHAT, json={"model": "ultra", "messages": []})
        await client.post(CHAT, json={"model": "my-local-alias", "messages": []})
    models = [b["model"] for b in env.fake.bodies()]
    assert models == [DEFAULT_MODELS["ultra"].model_id, DEFAULT_MODELS["nano"].model_id]


async def test_missing_run_id_is_generated_and_echoed(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        res = await client.post(CHAT, json={"messages": []})
        run_id = res.headers["X-Ratchet-Run-Id"]
        listed = (await client.get(f"/runs/{run_id}/steps")).json()
    assert run_id.startswith("run_")
    assert listed["run_id"] == run_id and [s["step_id"] for s in listed["steps"]] == ["m001"]


async def test_path_form_run_id_attributes_the_call(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        res = await client.post("/v1/r/run_path/chat/completions", json={"messages": []})
        bad = await client.get("/runs/..bad/steps")
    assert res.headers["X-Ratchet-Run-Id"] == "run_path"
    assert len(_steps(env, "run_path")) == 1
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"


async def test_tool_calls_requested_are_dotted_names(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "github__close_issue", "arguments": "{}"},
        }
    ]
    env.fake.push(fu.chat_json("", tool_calls=calls))
    async with env.client() as client:
        await client.post(CHAT, json={"messages": []}, headers={"X-Ratchet-Run-Id": "run_tools"})
    (step,) = _steps(env, "run_tools")
    assert step.tool_calls_requested == ["github.close_issue"]


async def test_no_secret_value_is_written_to_step_files_or_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The live key never reaches the trace store or the log stream."""
    env = fu.make_env(tmp_path)
    leak = {"role": "user", "content": f"my key is {fu.FAKE_KEY}", "api_key": "sk-abc"}
    async with env.client() as client:
        await client.post(
            CHAT,
            json={"messages": [leak], "api_key": fu.FAKE_KEY},
            headers={"X-Ratchet-Run-Id": "run_secret", "Authorization": f"Bearer {fu.FAKE_KEY}"},
        )
        env.fake.push(fu.sse(fu.sse_chunks([f"echo {fu.FAKE_KEY}"])))
        await client.post(
            CHAT,
            json={"messages": [leak], "stream": True},
            headers={"X-Ratchet-Run-Id": "run_secret"},
        )
    written = [p for p in env.data_dir.rglob("*") if p.is_file()]
    assert any(p.name.endswith(".req.json") for p in written)
    for path in written:
        assert fu.FAKE_KEY.encode() not in path.read_bytes(), path
    req = json.loads((env.store.trace_dir("run_secret") / "steps" / "m001.req.json").read_text())
    assert "api_key" not in req and "<SECRET>" in req["messages"][0]["content"]
    assert fu.FAKE_KEY not in capsys.readouterr().out


async def test_healthz_and_metrics(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        health = (await client.get("/healthz")).json()
        await client.post(
            CHAT,
            json={"messages": []},
            headers={"X-Ratchet-Purpose": "screen", "X-Ratchet-Tier": "nano"},
        )
        metrics = (await client.get("/metrics")).text
    assert health == {"ok": True, "upstream": env.settings.nebius_tf_base_url}
    assert 'ratchet_model_calls_total{purpose="screen",status="ok",tier="nano"} 1.0' in metrics
    assert "ratchet_model_usd_total" in metrics and "ratchet_model_latency_ms_bucket" in metrics


async def test_simulator_upstream_gets_prompt_and_purpose_tags(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_upstream="sim", nebius_token_factory_key=None)
    headers = {
        "X-Ratchet-Purpose": "check",
        "X-Ratchet-Prompt": "check@0123456789ab",
        "X-Ratchet-Tier": "super",
    }
    async with env.client() as client:
        health = (await client.get("/healthz")).json()
        res = await client.post(CHAT, json={"messages": []}, headers=headers)
    assert res.status_code == 200 and health["upstream"] == "sim"
    sent = env.fake.requests[0]
    assert sent.headers["x-ratchet-prompt"] == "check@0123456789ab"
    assert sent.headers["x-ratchet-purpose"] == "check"
    assert "authorization" not in sent.headers
    assert env.fake.bodies()[0]["model"] == "ratchet-sim/super"


async def test_responses_api_is_flagged_and_recorded_as_responses(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        off = await client.post("/v1/responses", json={"input": "hi"})
    assert off.status_code == 404 and off.json()["error"]["code"] == "not_found"

    env = fu.make_env(tmp_path / "on", ratchet_feature_responses_api=True)
    final = {
        "id": "resp_1",
        "object": "response",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            {"type": "function_call", "name": "gmail__send", "arguments": "{}"},
        ],
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }
    env.fake.push(lambda request: fu.httpx.Response(200, json=final))
    async with env.client() as client:
        res = await client.post(
            "/v1/responses", json={"input": "hi"}, headers={"X-Ratchet-Run-Id": "run_resp"}
        )
    assert res.status_code == 200 and res.json() == final
    assert str(env.fake.requests[0].url).endswith("/v1/responses")
    (step,) = _steps(env, "run_resp")
    assert step.api == "responses" and step.content_preview == "hello"
    assert step.tool_calls_requested == ["gmail.send"]
    assert (step.prompt_tokens, step.completion_tokens) == (12, 3)


async def test_invalid_json_body_is_a_bad_request(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        res = await client.post(CHAT, content=b"[1,2", headers={"content-type": "application/json"})
    assert res.status_code == 400
    error = res.json()["error"]
    assert error["code"] == "bad_request" and error["request_id"] == res.headers["X-Request-Id"]


async def test_broken_stream_still_records_what_arrived(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    chunks = fu.sse_chunks(["par", "tial"])[:2]

    class Broken(fu.httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            for chunk in chunks:
                yield chunk
            raise fu.httpx.ReadError("connection reset")

    env.fake.push(
        lambda request: fu.httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Broken()
        )
    )
    body = {"messages": [], "stream": True}
    async with env.client() as client:
        with pytest.raises(fu.httpx.ReadError):
            await client.post(CHAT, json=body, headers={"X-Ratchet-Run-Id": "run_broken"})
    (step,) = _steps(env, "run_broken")
    assert step.content_preview == "partial" and step.usage_estimated


async def test_models_list_the_tier_model_ids_for_hermes_probes(tmp_path: Path) -> None:
    """Hermes GETs <base_url>/models before its first call."""
    live = fu.make_env(tmp_path / "live")
    async with live.client() as client:
        plain = (await client.get("/v1/models")).json()
        per_run = (await client.get("/v1/r/run_m1/models")).json()
    assert plain == per_run and plain["object"] == "list"
    ids = [m["id"] for m in plain["data"]]
    assert ids == [model_for(t, live.settings) for t in ("nano", "super", "ultra")]
    assert plain["data"][1]["context_length"] == 256_000
    sim = fu.make_env(tmp_path / "sim", ratchet_upstream="sim", nebius_token_factory_key=None)
    async with sim.client() as client:
        sim_ids = [m["id"] for m in (await client.get("/v1/models")).json()["data"]]
    assert sim_ids == ["ratchet-sim/nano", "ratchet-sim/super", "ratchet-sim/ultra"]
