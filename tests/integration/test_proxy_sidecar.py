"""Proxy hardening: replay-worker sidecar mode, secret redaction, agent temperature, embeddings.

Covers caps against the dispatch-time allowance (not an empty scratch store), the sidecar
forcing purpose/tier, the admin route token, stream and cache redaction, embeddings caps and
error redaction, and the agent temperature default.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from pydantic import SecretStr

from ratchet.capture.runs import RunMeta, write_run_meta
from ratchet.config.models import model_for
from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.proxy.app import create_app
from ratchet.proxy.policy import SidecarPolicy, remaining_allowance, split_allowance
from ratchet.proxy.redact import StreamRedactor
from ratchet.store import CostRow, Store


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
BODY = {"messages": [{"role": "user", "content": "triage"}], "max_tokens": 50}
TOKEN = "admin-token-for-tests-0123"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_settings()


def _sidecar_env(tmp_path: Path, policy: SidecarPolicy, **overrides: object) -> object:
    env = fu.make_env(tmp_path, **overrides)
    env.app = create_app(  # type: ignore[attr-defined]
        env.settings,  # type: ignore[attr-defined]
        env.store,  # type: ignore[attr-defined]
        env.fake.transport,  # type: ignore[attr-defined]
        retry_backoff_s=0.0,
        sidecar=policy,
    )
    write_run_meta(env.store, RunMeta(run_id="run_s", agent_tier="super", seed=3))  # type: ignore[attr-defined]
    return env


def _spend(store: Store, usd: float, purpose: str = "agent", call_id: str = "c1") -> None:
    store.record_cost(CostRow(call_id, "run_x", None, "super", "m", 1, 1, usd, purpose))


# --- the sandboxed caller's purpose and tier tags are not trusted ----------------------------
async def test_sidecar_forces_agent_purpose_and_run_tier_whatever_the_headers(
    tmp_path: Path,
) -> None:
    env = _sidecar_env(tmp_path, SidecarPolicy(agent_tier="super"))
    headers = {
        "X-Ratchet-Run-Id": "run_s",
        "X-Ratchet-Purpose": "adjudicate",
        "X-Ratchet-Tier": "ultra",
    }
    async with env.client() as client:  # type: ignore[attr-defined]
        first = await client.post(CHAT, json=BODY, headers=headers)
        second = await client.post(CHAT, json=BODY, headers=headers)
    assert first.status_code == 200 and second.status_code == 200
    bodies = env.fake.bodies()  # type: ignore[attr-defined]
    assert len(bodies) == 2  # agent calls are never served from the cache
    assert {b["model"] for b in bodies} == {model_for("super", env.settings)}  # type: ignore[attr-defined]
    rows = env.store.query("SELECT purpose, tier FROM costs")  # type: ignore[attr-defined]
    assert {(r["purpose"], r["tier"]) for r in rows} == {("agent", "super")}


# --- caps travel with the batch -------------------------------------------------------------
async def test_sidecar_refuses_once_the_dispatch_allowance_is_spent(tmp_path: Path) -> None:
    env = _sidecar_env(tmp_path, SidecarPolicy(agent_tier="super", allowance_usd={"daily": 0.5}))
    headers = {"X-Ratchet-Run-Id": "run_s"}
    async with env.client() as client:  # type: ignore[attr-defined]
        ok = await client.post(CHAT, json=BODY, headers=headers)
        _spend(env.store, 0.6)  # type: ignore[attr-defined]
        refused = await client.post(CHAT, json=BODY, headers=headers)
    assert ok.status_code == 200
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "budget_exceeded"
    assert len(env.fake.requests) == 1  # type: ignore[attr-defined]


async def test_sidecar_with_no_allowance_left_refuses_on_an_empty_scratch_store(
    tmp_path: Path,
) -> None:
    env = _sidecar_env(
        tmp_path, SidecarPolicy(agent_tier="super", allowance_usd={"daily": 0.0, "agent": 1.0})
    )
    async with env.client() as client:  # type: ignore[attr-defined]
        res = await client.post(CHAT, json=BODY, headers={"X-Ratchet-Run-Id": "run_s"})
    assert res.status_code == 429
    assert env.fake.requests == []  # type: ignore[attr-defined]


def test_remaining_allowance_comes_from_the_control_plane_ledger(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ratchet_data_dir=tmp_path / "d",
        ratchet_daily_usd_cap=10.0,
        ratchet_bench_usd_cap=12.0,
        ratchet_purpose_usd_caps={"agent": 2.0},
    )
    store = Store.open(settings.ratchet_data_dir)
    _spend(store, 9.5, "adjudicate", "a")
    _spend(store, 1.5, "agent", "b")
    allowance = remaining_allowance(store, settings)
    assert allowance["daily"] == 0.0  # 11 spent of 10: never negative
    assert allowance["agent"] == pytest.approx(0.5)
    assert allowance["bench"] == pytest.approx(12.0)
    assert split_allowance({"daily": 3.0}, 3) == {"daily": 1.0}


# --- admin routes are for the worker --------------------------------------------------------
async def test_sidecar_steps_route_needs_the_worker_admin_token(tmp_path: Path) -> None:
    env = _sidecar_env(tmp_path, SidecarPolicy(agent_tier="super", admin_token=TOKEN))
    async with env.client() as client:  # type: ignore[attr-defined]
        anonymous = await client.get("/runs/run_s/steps")
        wrong = await client.get("/runs/run_s/steps", headers={"X-Ratchet-Admin-Token": "no"})
        worker = await client.get("/runs/run_s/steps", headers={"X-Ratchet-Admin-Token": TOKEN})
    assert anonymous.status_code == 403 and wrong.status_code == 403
    assert anonymous.json()["error"]["code"] == "sidecar_token_invalid"
    assert worker.status_code == 200


async def test_control_plane_steps_route_stays_open(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        res = await client.get("/runs/run_s/steps")
    assert res.status_code == 200


# --- agent temperature ----------------------------------------------------------------------
async def test_agent_call_without_temperature_gets_zero_and_explicit_value_is_kept(
    tmp_path: Path,
) -> None:
    env = fu.make_env(tmp_path, ratchet_feature_responses_api=True)
    responses_reply = httpx.Response(200, json={"output_text": "ok", "usage": {}})
    env.fake.push(fu.chat_json(), fu.chat_json(), lambda _: responses_reply)
    headers = {"X-Ratchet-Run-Id": "run_t"}
    async with env.client() as client:
        await client.post(CHAT, json=BODY, headers=headers)
        await client.post(CHAT, json={**BODY, "temperature": 0.7}, headers=headers)
        await client.post("/v1/responses", json={"input": "hi"}, headers=headers)
    plain, explicit, responses = env.fake.bodies()
    assert plain["temperature"] == 0
    assert explicit["temperature"] == 0.7
    assert responses["temperature"] == 0


# --- streamed chunks and cached payloads are redacted ---------------------------------------
async def test_secret_split_across_stream_chunks_is_redacted(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    key = fu.FAKE_KEY
    raw = b"".join(fu.sse_chunks([f"upstream echoed {key} here"]))
    cut = raw.index(key.encode()) + 10  # split the key across two network chunks
    env.fake.push(fu.sse([raw[:cut], raw[cut:]]))
    async with env.client() as client:
        res = await client.post(
            CHAT, json={**BODY, "stream": True}, headers={"X-Ratchet-Run-Id": "run_st"}
        )
    assert res.status_code == 200
    assert key not in res.text and key[10:] not in res.text
    assert "<SECRET>" in res.text


def test_stream_redactor_holds_back_only_a_possible_secret_prefix() -> None:
    redactor = StreamRedactor(("abcdefgh",))
    out = redactor.feed(b"hello abcd") + redactor.feed(b"efgh world") + redactor.flush()
    assert out == b"hello <SECRET> world"


async def test_cached_payload_and_cache_hit_hold_no_secret(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    env.fake.default = fu.chat_json(f"echo {fu.FAKE_KEY}")
    headers = {"X-Ratchet-Run-Id": "judge_c", "X-Ratchet-Purpose": "screen"}
    async with env.client() as client:
        first = await client.post(CHAT, json=BODY, headers=headers)
        hit = await client.post(CHAT, json=BODY, headers=headers)
    assert hit.headers["X-Ratchet-Cached"] == "1"
    assert fu.FAKE_KEY not in first.text and fu.FAKE_KEY not in hit.text
    cached = env.store.query("SELECT * FROM response_cache")
    assert cached and fu.FAKE_KEY not in json.dumps([dict(r) for r in cached], default=str)


# --- embeddings ----------------------------------------------------------------------------
def _embed_env(tmp_path: Path, reply: httpx.Response, **overrides: object) -> tuple[object, Store]:
    settings = configure(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            ratchet_data_dir=tmp_path / "data",
            ratchet_upstream="live",
            nebius_token_factory_key=SecretStr(fu.FAKE_KEY),
            **overrides,  # type: ignore[arg-type]
        )
    )
    store = Store.open(settings.ratchet_data_dir)
    app = create_app(settings, store, httpx.MockTransport(lambda _: reply), retry_backoff_s=0)
    return app, store


async def test_embeddings_respect_the_spending_caps(tmp_path: Path) -> None:
    ok = httpx.Response(200, json={"data": [], "usage": {"prompt_tokens": 1}})
    app, store = _embed_env(tmp_path, ok, ratchet_purpose_usd_caps={"embed": 0.0})
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        res = await client.post("/v1/embeddings", json={"input": ["x"]})
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "budget_exceeded"


async def test_embeddings_error_passthrough_redacts_the_key(tmp_path: Path) -> None:
    echo = httpx.Response(401, json={"error": {"message": f"bad key {fu.FAKE_KEY}"}})
    app, _ = _embed_env(tmp_path, echo)
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy") as client:
        res = await client.post("/v1/embeddings", json={"input": ["x"]})
    assert res.status_code == 401
    assert fu.FAKE_KEY not in res.text and "<SECRET>" in res.text
