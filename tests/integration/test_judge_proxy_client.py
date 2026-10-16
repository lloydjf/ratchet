"""call_structured through the real ModelClient against a respx-mocked capture proxy.

Checks the wire contract (headers, response_format, thinking) and the response_format
degradation path when the proxy answers 4xx ``upstream_rejected``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import respx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

from judge_fakes import opinion, settings  # noqa: E402

from ratchet.judge import structured  # noqa: E402
from ratchet.judge.schemas import AdjudicateOut, ScreenOut  # noqa: E402
from ratchet.llm.client import ModelClient  # noqa: E402

PROXY = "http://proxy.test/v1"
URL = f"{PROXY}/chat/completions"
INPUTS = {
    "task_input": "t", "criteria": "c", "golden_output": "g", "replay_output": "r",
    "golden_calls": "a", "replay_calls": "b",
}  # fmt: skip


def _ok(content: str, *, reasoning: str | None = None) -> httpx.Response:
    message: dict[str, object] = {"role": "assistant", "content": content}
    if reasoning:
        message["reasoning_content"] = reasoning
    return httpx.Response(
        200,
        json={"choices": [{"message": message}], "usage": {"prompt_tokens": 900,
              "completion_tokens": 60}},
        headers={"X-Ratchet-Usd": "0.0000684", "X-Ratchet-Step-Id": "m004",
                 "X-Ratchet-Model": "ratchet-sim/nano"},
    )  # fmt: skip


def _rejected() -> httpx.Response:
    return httpx.Response(
        400, json={"error": {"code": "upstream_rejected", "message": "response_format unsupported"}}
    )


@respx.mock
async def test_wire_contract_headers_and_json_schema(tmp_path: Path) -> None:
    route = respx.post(URL).mock(return_value=_ok(opinion("equivalent", 0.9)))
    with settings(tmp_path, ratchet_proxy_url=PROXY):
        async with ModelClient(run_id="judge_e1", edit_id="e1") as client:
            result = await structured.call_structured(
                client, prompt="screen", tier="nano", inputs=INPUTS, schema=ScreenOut,
                max_tokens=300,
            )  # fmt: skip
    assert result.value is not None and result.usd == 0.0000684 and result.step_ids == ["m004"]
    request = route.calls[0].request
    assert request.headers["X-Ratchet-Prompt"].startswith("screen@")
    assert request.headers["X-Ratchet-Purpose"] == "screen"
    assert request.headers["X-Ratchet-Tier"] == "nano"
    assert request.headers["X-Ratchet-Thinking"] == "off"
    body = json.loads(request.content)
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["temperature"] == 0 and body["max_tokens"] == 300


@respx.mock
async def test_proxy_rejection_degrades_and_remembers_the_mode(tmp_path: Path) -> None:
    route = respx.post(URL).mock(
        side_effect=[_rejected(), _rejected(), _ok("<think>x</think>" + opinion("regressed", 0.9)),
                     _ok(opinion("regressed", 0.9))]
    )  # fmt: skip
    with settings(tmp_path, ratchet_proxy_url=PROXY):
        async with ModelClient(run_id="judge_e1") as client:
            first = await structured.call_structured(
                client, prompt="screen", tier="nano", inputs=INPUTS, schema=ScreenOut,
                max_tokens=300,
            )  # fmt: skip
            second = await structured.call_structured(
                client, prompt="screen", tier="nano", inputs=INPUTS, schema=ScreenOut,
                max_tokens=300,
            )  # fmt: skip
    formats = [json.loads(c.request.content).get("response_format") for c in route.calls]
    assert formats == [formats[0], {"type": "json_object"}, None, None]
    assert formats[0]["type"] == "json_schema"
    assert first.mode == "prompt" and second.mode == "prompt" and first.attempts == 1
    assert first.value is not None and first.value.verdict == "regressed"


@respx.mock
async def test_ultra_reasoning_arrives_separately_and_thinking_headers_are_set(
    tmp_path: Path,
) -> None:
    route = respx.post(URL).mock(
        return_value=_ok(opinion("regressed", 0.8), reasoning="long chain of thought {not json}")
    )
    inputs = dict.fromkeys(
        ["task_input", "skill_diff", "criteria", "golden_steps", "replay_steps", "golden_output",
         "replay_output", "level1", "level2"], "x",
    )  # fmt: skip
    with settings(tmp_path, ratchet_proxy_url=PROXY):
        async with ModelClient(run_id="judge_e1") as client:
            result = await structured.call_structured(
                client, prompt="adjudicate", tier="ultra", inputs=inputs, schema=AdjudicateOut,
                max_tokens=1200, reasoning_budget=4000,
            )  # fmt: skip
    assert result.value is not None and result.value.verdict == "regressed"
    request = route.calls[0].request
    assert request.headers["X-Ratchet-Thinking"] == "on"
    assert request.headers["X-Ratchet-Reasoning-Budget"] == "4000"
    assert json.loads(request.content)["max_tokens"] == 5200


@respx.mock
async def test_ultra_timeout_maps_to_judge_timeout(tmp_path: Path) -> None:
    respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with settings(tmp_path, ratchet_proxy_url=PROXY):
        async with ModelClient(run_id="judge_e1") as client:
            result = await structured.call_structured(
                client, prompt="screen", tier="ultra", inputs=INPUTS, schema=ScreenOut,
                max_tokens=300,
            )  # fmt: skip
    assert result.value is None and result.failure_code == "judge_timeout"
