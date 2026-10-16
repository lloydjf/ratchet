"""ModelClient end to end through the real proxy app and a fake Token Factory."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from ratchet.capture.runs import RunMeta, write_run_meta
from ratchet.config.models import model_for
from ratchet.config.pricing import cost_usd
from ratchet.config.settings import reset_settings
from ratchet.errors import BudgetExceeded, ConflictError, UpstreamError
from ratchet.llm.client import ModelClient
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
MESSAGES: list[dict[str, object]] = [{"role": "user", "content": "judge this"}]


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    yield
    reset_settings()


def _client(env: object, run_id: str = "judge_edit_1") -> ModelClient:
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=env.app))  # type: ignore[attr-defined]
    return ModelClient(
        run_id=run_id,
        edit_id="edit_1",
        session_hash="sess",
        settings=env.settings,  # type: ignore[attr-defined]
        http=http,
    )


async def test_chat_returns_cost_step_and_model_from_proxy_headers(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with _client(env) as client:
        result = await client.chat(
            tier="nano",
            purpose="screen",
            messages=MESSAGES,
            max_tokens=300,
            thinking=False,
            prompt_name="screen@abc",
        )
    assert result.text == "ok" and result.step_id == "m001" and not result.cached
    assert result.model == model_for("nano", env.settings)
    assert result.usd == pytest.approx(cost_usd("nano", 1000, 200))
    sent = env.fake.bodies()[0]
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["temperature"] == 0


async def test_second_identical_judge_call_is_cached(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    async with _client(env) as client:
        await client.chat(tier="super", purpose="check", messages=MESSAGES, max_tokens=900)
        again = await client.chat(tier="super", purpose="check", messages=MESSAGES, max_tokens=900)
    assert again.cached and again.usd == 0.0 and len(env.fake.requests) == 1


async def test_tier_mismatch_raises_conflict_with_code(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    write_run_meta(env.store, RunMeta(run_id="run_a", agent_tier="super", seed=1))
    async with _client(env, run_id="run_a") as client:
        with pytest.raises(ConflictError) as info:
            await client.chat(tier="nano", purpose="agent", messages=MESSAGES, max_tokens=10)
    assert info.value.code == "tier_mismatch"


async def test_budget_refusal_raises_budget_exceeded(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path, ratchet_daily_usd_cap=0.5)
    env.store.record_cost(CostRow("c", "r", None, "ultra", "m", 1, 1, 0.5, "adjudicate"))
    async with _client(env) as client:
        with pytest.raises(BudgetExceeded):
            await client.chat(tier="ultra", purpose="adjudicate", messages=MESSAGES, max_tokens=10)


async def test_upstream_rate_limit_is_an_upstream_error_not_a_budget_stop(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    env.fake.push(fu.status(429, {"error": {"message": "slow down"}}))
    async with _client(env) as client:
        with pytest.raises(UpstreamError) as info:
            await client.chat(tier="nano", purpose="screen", messages=MESSAGES, max_tokens=10)
    assert info.value.code == "upstream_unavailable" and info.value.retryable


async def test_upstream_outage_raises_upstream_unavailable(tmp_path: Path) -> None:
    env = fu.make_env(tmp_path)
    env.fake.push(fu.status(503), fu.status(503))
    async with _client(env) as client:
        with pytest.raises(UpstreamError) as info:
            await client.chat(tier="super", purpose="check", messages=MESSAGES, max_tokens=10)
    assert info.value.code == "upstream_unavailable"


async def test_client_waits_long_enough_for_the_proxy_retry() -> None:
    """The proxy may spend two full attempts plus ~2.5 s backoff on one call, so
    the client's timeout must exceed that or a slow first attempt aborts the retry."""
    from ratchet.config.models import spec_for
    from ratchet.config.settings import Settings
    from ratchet.llm.client import RETRY_BACKOFF_MAX_S, proxy_deadline_s

    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.extensions["timeout"])
        payload = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
        return httpx.Response(200, json=payload)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = ModelClient(run_id="judge_t", settings=settings, http=http)
    await client.chat(tier="super", purpose="check", messages=MESSAGES, max_tokens=10)
    await http.aclose()
    attempt = spec_for("super", settings).timeout_s
    assert float(seen["read"]) >= 2 * attempt + RETRY_BACKOFF_MAX_S  # type: ignore[arg-type]
    assert proxy_deadline_s(300.0) > 600.0
