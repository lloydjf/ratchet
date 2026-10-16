"""Proxy policy: tier_mismatch, budget caps, retries, 4xx passthrough, cache."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest

from ratchet import clock
from ratchet.capture.runs import RunMeta, write_run_meta
from ratchet.config.settings import reset_settings
from ratchet.proxy.policy import Limiter
from ratchet.store import CostRow


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
BODY = {"messages": [{"role": "user", "content": "compare these"}], "max_tokens": 300}


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_settings()


def _spend(env: object, usd: float, purpose: str = "agent", call_id: str = "c1") -> None:
    env.store.record_cost(  # type: ignore[attr-defined]
        CostRow(call_id, "run_x", None, "super", "m", 1, 1, usd, purpose)
    )


async def test_agent_call_on_other_tier_than_run_is_refused(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    write_run_meta(env.store, RunMeta(run_id="run_r", agent_tier="super", seed=11))
    headers = {"X-Ratchet-Run-Id": "run_r", "X-Ratchet-Tier": "ultra", "X-Request-Id": "req_1"}
    async with env.client() as client:
        res = await client.post(CHAT, json=BODY, headers=headers)
        judged = await client.post(
            CHAT, json=BODY, headers={**headers, "X-Ratchet-Purpose": "adjudicate"}
        )
    assert res.status_code == 409
    assert res.json() == {
        "error": {
            "code": "tier_mismatch",
            "message": "model tier differs from the run's agent tier",
            "request_id": "req_1",
        }
    }
    assert res.headers["X-Ratchet-Run-Id"] == "run_r"
    assert judged.status_code == 200  # judge purposes may use any tier
    assert len(env.fake.requests) == 1


async def test_seed_comes_from_run_meta_when_absent(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    write_run_meta(env.store, RunMeta(run_id="run_s", agent_tier="super", seed=11))
    async with env.client() as client:
        await client.post(CHAT, json=BODY, headers={"X-Ratchet-Run-Id": "run_s"})
        await client.post(CHAT, json={**BODY, "seed": 5}, headers={"X-Ratchet-Run-Id": "run_s"})
    assert [b["seed"] for b in env.fake.bodies()] == [11, 5]


async def test_daily_cap_refuses_before_forwarding(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_daily_usd_cap=1.0)
    _spend(env, 1.0)
    async with env.client() as client:
        res = await client.post(CHAT, json=BODY)
    assert res.status_code == 429 and res.json()["error"]["code"] == "budget_exceeded"
    assert env.fake.requests == []


async def test_purpose_cap_only_blocks_that_purpose(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_purpose_usd_caps={"adjudicate": 0.5})
    _spend(env, 0.6, purpose="adjudicate")
    async with env.client() as client:
        blocked = await client.post(CHAT, json=BODY, headers={"X-Ratchet-Purpose": "adjudicate"})
        allowed = await client.post(CHAT, json=BODY, headers={"X-Ratchet-Purpose": "screen"})
    assert blocked.status_code == 429 and allowed.status_code == 200


async def test_bench_cap_is_a_hard_all_time_cap(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_bench_usd_cap=2.0, ratchet_daily_usd_cap=100.0)
    with clock.frozen(clock.now() - timedelta(days=3)):
        _spend(env, 2.5, purpose="bench")
    async with env.client() as client:
        bench = await client.post(CHAT, json=BODY, headers={"X-Ratchet-Purpose": "bench"})
        agent = await client.post(CHAT, json=BODY)
    assert bench.status_code == 429 and agent.status_code == 200


async def test_spend_from_yesterday_does_not_count_toward_daily_cap(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_daily_usd_cap=1.0)
    with clock.frozen(clock.now() - timedelta(days=1, hours=1)):
        _spend(env, 5.0)
    async with env.client() as client:
        assert (await client.post(CHAT, json=BODY)).status_code == 200


async def test_5xx_is_retried_once_then_succeeds(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    env.fake.push(fu.status(503))
    async with env.client() as client:
        res = await client.post(CHAT, json=BODY, headers={"X-Ratchet-Run-Id": "run_retry"})
    assert res.status_code == 200 and len(env.fake.requests) == 2
    assert res.headers["X-Ratchet-Step-Id"] == "m001"


@pytest.mark.parametrize("failure", ["status", "timeout"])
async def test_second_failure_becomes_502_upstream_unavailable(
    tmp_path: Path, failure: str
) -> None:
    env = fu.make_env(tmp_path)
    reply = fu.status(500) if failure == "status" else fu.timeout()
    env.fake.push(reply, reply)
    async with env.client() as client:
        res = await client.post(CHAT, json=BODY, headers={"X-Ratchet-Run-Id": "run_fail"})
        metrics = (await client.get("/metrics")).text
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "upstream_unavailable"
    assert len(env.fake.requests) == 2
    assert 'status="upstream_unavailable"' in metrics


@pytest.mark.parametrize("stream", [False, True])
async def test_4xx_is_passed_through_unchanged(tmp_path: Path, stream: bool) -> None:
    env = fu.make_env(tmp_path)
    body = {"error": {"message": "max_tokens too large", "type": "invalid_request_error"}}
    env.fake.push(fu.status(400, body))
    async with env.client() as client:
        res = await client.post(
            CHAT, json={**BODY, "stream": stream}, headers={"X-Ratchet-Run-Id": "run_4"}
        )
    assert res.status_code == 400 and res.json() == body
    assert res.headers["X-Ratchet-Run-Id"] == "run_4"
    assert len(env.fake.requests) == 1
    assert env.store.collect_steps("run_4") == []


async def test_live_upstream_without_key_is_a_config_error(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, nebius_token_factory_key=None)
    async with env.client() as client:
        res = await client.post(CHAT, json=BODY)
    assert res.status_code == 500 and res.json()["error"]["code"] == "config_error"


async def test_non_agent_cache_hit_is_free_and_recorded(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    headers = {"X-Ratchet-Run-Id": "run_c", "X-Ratchet-Purpose": "screen", "X-Ratchet-Tier": "nano"}
    async with env.client() as client:
        first = await client.post(CHAT, json=BODY, headers=headers)
        second = await client.post(CHAT, json=BODY, headers=headers)
        thinking = await client.post(
            CHAT, json=BODY, headers={**headers, "X-Ratchet-Thinking": "on"}
        )
    assert len(env.fake.requests) == 2  # the thinking-on variant is a different cache key
    assert first.headers["X-Ratchet-Cached"] == "0" and second.headers["X-Ratchet-Cached"] == "1"
    assert float(second.headers["X-Ratchet-Usd"]) == 0.0
    assert second.json()["choices"] == first.json()["choices"]
    assert thinking.headers["X-Ratchet-Cached"] == "0"
    steps = env.store.collect_steps("run_c")
    assert [s.step_id for s in steps] == ["m001", "m002", "m003"]
    assert steps[1].usd == 0.0  # type: ignore[union-attr]
    rows = env.store.query("SELECT cached, usd, prompt_tokens FROM costs ORDER BY call_id")
    assert [(r["cached"], r["usd"], r["prompt_tokens"]) for r in rows][1] == (1, 0.0, 0)


async def test_cache_hit_is_served_even_over_budget(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_daily_usd_cap=0.0001)
    headers = {"X-Ratchet-Purpose": "check", "X-Ratchet-Tier": "nano"}
    async with env.client() as client:
        assert (await client.post(CHAT, json=BODY, headers=headers)).status_code == 200
        assert (await client.post(CHAT, json=BODY, headers=headers)).status_code == 200
        fresh = await client.post(CHAT, json={**BODY, "max_tokens": 9}, headers=headers)
    assert fresh.status_code == 429 and len(env.fake.requests) == 1


async def test_agent_calls_are_never_cached(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        await client.post(CHAT, json=BODY)
        res = await client.post(CHAT, json=BODY)
    assert res.headers["X-Ratchet-Cached"] == "0" and len(env.fake.requests) == 2


async def test_judge_purposes_are_forced_to_temperature_zero(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with env.client() as client:
        await client.post(
            CHAT, json={**BODY, "temperature": 0.9}, headers={"X-Ratchet-Purpose": "check"}
        )
        await client.post(CHAT, json={**BODY, "temperature": 0.9})
    assert [b["temperature"] for b in env.fake.bodies()] == [0, 0.9]


def test_concurrency_caps_per_purpose() -> None:
    limiter = Limiter()
    caps = {
        p: limiter.limit(p) for p in ("agent", "screen", "check", "adjudicate", "bench", "extract")
    }
    assert caps == {"agent": 8, "screen": 16, "check": 4, "adjudicate": 4, "bench": 8, "extract": 8}


async def test_adjudicate_calls_never_exceed_two_in_flight(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    in_flight = 0
    peak = 0
    reply = fu.chat_json()

    async def slow(request: fu.httpx.Request) -> fu.httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return reply(request)

    env.fake.default = slow
    headers = {"X-Ratchet-Purpose": "adjudicate", "X-Ratchet-Tier": "ultra"}
    async with env.client() as client:
        results = await asyncio.gather(
            *(client.post(CHAT, json={**BODY, "seed": i}, headers=headers) for i in range(10))
        )
    assert all(r.status_code == 200 for r in results)
    assert peak == 4
