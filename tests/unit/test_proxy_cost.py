"""Computed USD matches hand-calculated values for 6 usage blocks."""

from __future__ import annotations

import pytest

from ratchet.config.pricing import cost_usd, counterfactual_ultra_usd
from ratchet.proxy.headers import parse_context
from ratchet.proxy.recorder import CallRecord, Usage, usage_from

# (tier, usage block, hand-computed USD = in * price_in / 1e6 + out * price_out / 1e6)
CASES = [
    ("nano", {"prompt_tokens": 8000, "completion_tokens": 500}, 0.00048 + 0.00012),
    ("nano", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}, 0.06 + 0.24),
    ("super", {"prompt_tokens": 12000, "completion_tokens": 1000}, 0.0036 + 0.0009),
    ("super", {"prompt_tokens": 25000, "completion_tokens": 5000}, 0.0075 + 0.0045),
    ("ultra", {"prompt_tokens": 15000, "completion_tokens": 2000}, 0.015 + 0.006),
    ("ultra", {"prompt_tokens": 0, "completion_tokens": 400}, 0.0012),
]


@pytest.mark.parametrize(("tier", "usage", "expected"), CASES)
def test_cost_matches_hand_calculation(tier: str, usage: dict[str, int], expected: float) -> None:
    response: dict[str, object] = {"choices": [], "usage": usage}
    parsed = usage_from(response, {}, "chat")
    assert not parsed.estimated
    assert cost_usd(tier, parsed.prompt_tokens, parsed.completion_tokens) == pytest.approx(  # type: ignore[arg-type]
        expected, rel=1e-12
    )


def test_counterfactual_prices_all_tokens_at_ultra() -> None:
    assert counterfactual_ultra_usd(12000, 1000) == pytest.approx(0.012 + 0.003)


def test_cached_call_costs_nothing() -> None:
    from ratchet import clock

    rec = CallRecord(
        ctx=parse_context({}),
        step_id="m001",
        tier="ultra",
        model="m",
        api="chat",
        request_body={},
        response_body={},
        request_hash="h",
        usage=Usage(1000, 1000),
        latency_ms=1,
        started_at=clock.now(),
        cached=True,
    )
    assert rec.usd == 0.0


def test_missing_usage_is_estimated_from_text() -> None:
    response: dict[str, object] = {"choices": [{"message": {"content": "x" * 40}}]}
    parsed = usage_from(response, {"messages": [{"role": "user", "content": "y" * 80}]}, "chat")
    assert parsed.estimated and parsed.completion_tokens == 10 and parsed.prompt_tokens > 20


def test_responses_api_usage_uses_input_and_output_tokens() -> None:
    parsed = usage_from({"usage": {"input_tokens": 5, "output_tokens": 3}}, {}, "responses")
    assert (parsed.prompt_tokens, parsed.completion_tokens, parsed.estimated) == (5, 3, False)
