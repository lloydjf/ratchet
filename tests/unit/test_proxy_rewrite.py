"""Request rewriting, tagging and hashing (determinism, the request path)."""

from __future__ import annotations

import pytest

from ratchet.config.models import model_for
from ratchet.config.settings import Settings
from ratchet.proxy.headers import BadRequest, parse_context, upstream_headers
from ratchet.proxy.rewrite import request_hash, resolve_tier, rewrite_chat, rewrite_responses


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, ratchet_upstream="live", **kw)  # type: ignore[arg-type]


def test_tier_header_wins_then_model_literal_then_agent_tier() -> None:
    s = _settings(ratchet_agent_tier="super")
    assert resolve_tier(parse_context({"X-Ratchet-Tier": "nano"}), "ultra", s) == "nano"
    assert resolve_tier(parse_context({}), "ultra", s) == "ultra"
    assert resolve_tier(parse_context({}), model_for("nano", s), s) == "nano"
    assert resolve_tier(parse_context({}), "gpt-4o", s) == "super"


def test_missing_run_id_is_generated_and_path_form_wins() -> None:
    ctx = parse_context({})
    assert ctx.run_id_generated and ctx.run_id.startswith("run_")
    ctx = parse_context({"X-Ratchet-Run-Id": "run_header"}, path_run_id="run_path")
    assert ctx.run_id == "run_path" and not ctx.run_id_generated


@pytest.mark.parametrize(
    "headers",
    [
        {"X-Ratchet-Run-Id": "../etc"},
        {"X-Ratchet-Tier": "mega"},
        {"X-Ratchet-Purpose": "mining"},
        {"X-Ratchet-Thinking": "maybe"},
        {"X-Ratchet-Reasoning-Budget": "-1"},
    ],
)
def test_bad_tags_are_refused(headers: dict[str, str]) -> None:
    with pytest.raises(BadRequest):
        parse_context(headers)


def test_rewrite_sets_model_seed_temperature_and_stream_usage() -> None:
    s = _settings()
    ctx = parse_context({"X-Ratchet-Purpose": "screen"})
    body: dict[str, object] = {"model": "nano", "messages": [], "temperature": 0.7, "stream": True}
    out = rewrite_chat(body, ctx=ctx, tier="nano", seed=42, settings=s)
    assert out["model"] == model_for("nano", s)
    assert out["seed"] == 42
    assert out["temperature"] == 0
    assert out["stream_options"] == {"include_usage": True}
    assert body["temperature"] == 0.7  # the caller's dict is not mutated


def test_rewrite_keeps_agent_temperature_and_explicit_seed() -> None:
    s = _settings()
    body: dict[str, object] = {"messages": [], "temperature": 0.6, "seed": 7}
    out = rewrite_chat(body, ctx=parse_context({}), tier="super", seed=42, settings=s)
    assert out["temperature"] == 0.6 and out["seed"] == 7
    assert "stream_options" not in out


def test_thinking_headers_map_through_thinking_params() -> None:
    s = _settings()
    ctx = parse_context({"X-Ratchet-Thinking": "off"})
    body: dict[str, object] = {"chat_template_kwargs": {"other": 1}}
    out = rewrite_chat(body, ctx=ctx, tier="ultra", seed=None, settings=s)
    assert out["chat_template_kwargs"] == {"other": 1, "enable_thinking": False}
    effort = _settings(ratchet_thinking_style="reasoning_effort")
    ctx = parse_context({"X-Ratchet-Thinking": "on", "X-Ratchet-Reasoning-Budget": "4000"})
    out = rewrite_chat({}, ctx=ctx, tier="ultra", seed=None, settings=effort)
    assert out["reasoning_effort"] == "high"
    out = rewrite_responses({}, ctx=ctx, tier="ultra", settings=effort)
    assert out["reasoning"] == {"effort": "high"}


def test_request_hash_ignores_volatile_fields_and_untracked_params() -> None:
    a: dict[str, object] = {
        "model": "x",
        "stream": True,
        "user": "u1",
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "r"},
        ],
    }
    b = {
        **a,
        "model": "y",
        "stream": False,
        "user": "u2",
        "messages": [
            {"role": "assistant", "tool_calls": [{"id": "call_9", "function": {"name": "f"}}]},
            {"role": "tool", "tool_call_id": "call_9", "content": "r"},
        ],
    }
    assert request_hash(a) == request_hash(b)
    assert request_hash(a) != request_hash({**a, "seed": 1})
    assert request_hash(a) != request_hash({**a, "max_tokens": 10})


def test_upstream_headers_never_copy_caller_auth_and_tag_only_the_simulator() -> None:
    ctx = parse_context({"X-Ratchet-Purpose": "check", "X-Ratchet-Prompt": "check@abc"})
    live = upstream_headers(ctx, api_key="real", simulator=False, stream=False)
    assert live["Authorization"] == "Bearer real"
    assert not any(k.lower().startswith("x-ratchet") for k in live)
    sim = upstream_headers(ctx, api_key=None, simulator=True, stream=True)
    assert "Authorization" not in sim
    assert sim["X-Ratchet-Prompt"] == "check@abc" and sim["X-Ratchet-Purpose"] == "check"


def test_agent_calls_get_thinking_off_by_default_policy() -> None:
    from ratchet.config.settings import Settings
    from ratchet.proxy.headers import CallContext
    from ratchet.proxy.rewrite import rewrite_chat

    settings = Settings(ratchet_agent_thinking="off", ratchet_thinking_style="chat_template_kwargs")
    ctx = CallContext(
        run_id="run_a",
        run_id_generated=False,
        tier_header=None,
        purpose="agent",
        edit_id=None,
        session_hash=None,
        prompt_name=None,
        thinking=None,
        reasoning_budget=None,
    )
    out = rewrite_chat({"messages": []}, ctx=ctx, tier="super", seed=None, settings=settings)
    assert out["chat_template_kwargs"] == {"enable_thinking": False}
    default = Settings(ratchet_agent_thinking="default")
    out = rewrite_chat({"messages": []}, ctx=ctx, tier="super", seed=None, settings=default)
    assert "chat_template_kwargs" not in out
