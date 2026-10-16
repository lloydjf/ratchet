"""The capture proxy in ``RATCHET_UPSTREAM=sim`` mode round-trips through the real simulator
(lazy ``ratchet.sim.app`` import, ASGI transport), and the golden recording path —
MinimalRunner → proxy → simulator → mock tools in record mode on the demo world — works
end to end (ADR 0018, CONTRACTS §3)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

from test_sim_support import SKILLS, WORLD, run, world_for  # noqa: E402

from ratchet.config.settings import Settings, configure, reset_settings  # noqa: E402
from ratchet.judge.schemas import ScreenOut  # noqa: E402
from ratchet.judge.structured import call_structured, reset_structured_modes  # noqa: E402
from ratchet.llm.client import ModelClient  # noqa: E402
from ratchet.mocktools.app import create_app as create_mock_app  # noqa: E402
from ratchet.mocktools.world import World  # noqa: E402
from ratchet.models import LLMCall, ToolCall  # noqa: E402
from ratchet.proxy.app import create_app as create_proxy_app  # noqa: E402
from ratchet.replay.harness.minimal import MinimalRunner  # noqa: E402
from ratchet.replay.harness.prompt import build_system_prompt  # noqa: E402
from ratchet.replay.harness.skills import load_skills, select_skills  # noqa: E402
from ratchet.store import Store  # noqa: E402
from ratchet.tools.catalog import openai_tools  # noqa: E402

FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
TASK = "Triage the open issues in acme/widgets."


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    reset_structured_modes()
    s = configure(
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            ratchet_upstream="sim",
            ratchet_data_dir=tmp_path / "data",
            ratchet_proxy_url="http://proxy/v1",
        )
    )
    yield s
    reset_settings()
    reset_structured_modes()


def _agent_body(stream: bool = False) -> dict[str, object]:
    skills = load_skills(SKILLS)
    system = build_system_prompt(skills, select_skills(TASK, skills), FROZEN)
    body: dict[str, object] = {
        "model": "super",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": TASK}],
        "tools": openai_tools(),
        "temperature": 0,
    }
    if stream:
        body["stream"] = True
    return body


async def test_agent_call_round_trips_through_the_simulator(settings: Settings) -> None:
    store = Store.open(settings.ratchet_data_dir)
    app = create_proxy_app(settings, store)
    headers = {"X-Ratchet-Tier": "super", "X-Ratchet-Purpose": "agent"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c:
        res = await c.post(
            "/v1/r/run_sim_agent/chat/completions", json=_agent_body(), headers=headers
        )
    assert res.status_code == 200
    assert res.headers["X-Ratchet-Model"] == "ratchet-sim/super"
    assert float(res.headers["X-Ratchet-Usd"]) > 0
    payload = res.json()
    (call,) = payload["choices"][0]["message"]["tool_calls"]
    assert call["id"] == "call_1" and call["function"]["name"] == "github__list_issues"
    (step,) = [s for s in store.collect_steps("run_sim_agent") if isinstance(s, LLMCall)]
    assert step.prompt_tokens == payload["usage"]["prompt_tokens"] > 1000
    assert step.tool_calls_requested and not step.usage_estimated


async def test_streamed_call_is_reassembled_with_simulator_usage(settings: Settings) -> None:
    store = Store.open(settings.ratchet_data_dir)
    app = create_proxy_app(settings, store)
    headers = {"X-Ratchet-Tier": "super", "X-Ratchet-Run-Id": "run_sim_stream"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c:
        res = await c.post("/v1/chat/completions", json=_agent_body(stream=True), headers=headers)
    assert res.status_code == 200
    assert b"data: [DONE]" in res.content
    (step,) = [s for s in store.collect_steps("run_sim_stream") if isinstance(s, LLMCall)]
    assert step.usage_estimated is False and step.completion_tokens > 0


_LIST_CALL = {
    "id": "call_1",
    "type": "function",
    "function": {"name": "github__list_issues", "arguments": '{"repo": "acme/widgets"}'},
}


@pytest.mark.parametrize("stream", [False, True])
async def test_prompt_cache_hits_reach_the_step_and_the_ledger(
    settings: Settings, stream: bool
) -> None:
    """A continuation turn re-sends its prefix: the simulator reports prompt-cache hits the
    way Token Factory does and the proxy records them, streamed or not."""
    store = Store.open(settings.ratchet_data_dir)
    app = create_proxy_app(settings, store)
    headers = {"X-Ratchet-Tier": "super", "X-Ratchet-Purpose": "agent"}
    first = _agent_body(stream=stream)
    messages = list(first["messages"])  # type: ignore[call-overload]
    turn = {"role": "assistant", "content": "", "tool_calls": [_LIST_CALL]}
    result = {"role": "tool", "tool_call_id": "call_1", "content": "[]"}
    later = {**first, "messages": [*messages, turn, result]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://p") as c:
        for body in (first, later):
            res = await c.post("/v1/r/run_cache/chat/completions", json=body, headers=headers)
            assert res.status_code == 200
    one, two = [s for s in store.collect_steps("run_cache") if isinstance(s, LLMCall)]
    assert one.cached_prompt_tokens == 0
    assert 0 < two.cached_prompt_tokens < two.prompt_tokens
    rows = store.query("SELECT cached_prompt_tokens AS c FROM costs WHERE run_id = 'run_cache'")
    assert sorted(int(r["c"]) for r in rows) == [0, two.cached_prompt_tokens]
    totals = store.spend()
    assert totals.cached_prompt_tokens["super"] == two.cached_prompt_tokens
    assert totals.cache_share == pytest.approx(
        two.cached_prompt_tokens / (one.prompt_tokens + two.prompt_tokens)
    )


async def test_judge_call_forwards_the_prompt_header_to_the_simulator(
    settings: Settings, tmp_path: Path
) -> None:
    store = Store.open(settings.ratchet_data_dir)
    app = create_proxy_app(settings, store)
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    golden = await run("What is 17% of 240?", world=world_for(tmp_path))
    async with ModelClient(run_id="judge_sim_proxy", settings=settings, http=http) as client:
        result = await call_structured(
            client,
            prompt="screen",
            tier="nano",
            schema=ScreenOut,
            max_tokens=300,
            inputs={
                "task_input": golden.task,
                "criteria": "- The output answers the task",
                "golden_output": golden.final_output,
                "replay_output": golden.final_output,
                "golden_calls": "(none)",
                "replay_calls": "(none)",
            },
        )
    await http.aclose()
    assert result.value is not None and result.attempts == 1
    assert (result.value.verdict, result.value.confidence) == ("equivalent", 0.95)
    assert result.prompt_tokens > 0 and result.usd > 0


class _Bridge(httpx.BaseTransport):
    """Sync transport for MinimalRunner: routes by host to in-process ASGI test clients."""

    def __init__(self, clients: dict[str, TestClient]) -> None:
        self.clients = clients

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        client = self.clients[request.url.host]
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        path = request.url.raw_path.decode()
        res = client.request(request.method, path, content=request.read(), headers=headers)
        return httpx.Response(res.status_code, headers=res.headers, content=res.content)


def test_minimal_runner_records_a_golden_through_proxy_sim_and_mock_tools(
    settings: Settings, tmp_path: Path
) -> None:
    store = Store.open(settings.ratchet_data_dir)
    proxy = create_proxy_app(settings, store)
    mock = create_mock_app(
        settings, store, mode="record", world=World.load(WORLD), enable_mcp=False
    )
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with TestClient(proxy) as proxy_client, TestClient(mock) as mock_client:
        runner = MinimalRunner(
            transport=_Bridge({"proxy": proxy_client, "mock": mock_client}), log_dir=tmp_path
        )
        runner.configure(
            proxy_url="http://proxy/v1",
            mock_tools_url="http://mock",
            skills_dir=SKILLS,
            workspace=workspace,
            frozen_time=FROZEN,
            seed=7,
            agent_tier="super",
            run_id="golden_triage_widgets",
        )
        result = runner.run_task(TASK, timeout_s=60)
    assert result.exit_ok and result.skills_invoked == ["github-triage"]
    assert result.final_output.startswith("Triaged acme/widgets: 5 open issues.")
    assert "#12: bug" in result.final_output.splitlines()
    steps = store.collect_steps("golden_triage_widgets")
    models = [s for s in steps if isinstance(s, LLMCall)]
    assert len(models) == 3 and all(m.model == "ratchet-sim/super" for m in models)
    tools = [s.tool for s in steps if isinstance(s, ToolCall)]
    assert tools == ["github.list_issues"] + ["github.label"] * 4 + ["github.comment"]
