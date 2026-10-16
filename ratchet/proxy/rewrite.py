"""Pure request rewriting: tier resolution, model id, thinking, seed, determinism.

The proxy copies the caller's body and changes only what policy requires: ``model`` becomes
the tier's model id, thinking headers become provider fields (one mapping function in
``ratchet.config.models``), a missing ``seed`` comes from the run, judge calls are pinned to
``temperature=0``, agent calls that send no temperature get ``0`` (Hermes sends none;
golden and replay must sample the same way), and streams ask for a usage block.
"""

from __future__ import annotations

from ratchet.config.models import model_for, thinking_params
from ratchet.config.settings import Settings
from ratchet.models import TIERS, Tier
from ratchet.proxy.headers import CallContext, parse_tier
from ratchet.proxy.jsonx import as_dict, as_list, deep_merge
from ratchet.util import hash_json

__all__ = [
    "resolve_tier",
    "rewrite_chat",
    "rewrite_responses",
    "request_hash",
    "control_fields",
    "HASHED_CHAT_FIELDS",
]

HASHED_CHAT_FIELDS = ("messages", "tools", "temperature", "seed", "max_tokens", "response_format")
_HASHED_RESPONSES_FIELDS = (
    "input",
    "instructions",
    "tools",
    "temperature",
    "seed",
    "max_output_tokens",
    "text",
)
_VOLATILE_MESSAGE_KEYS = frozenset({"tool_call_id", "timestamp", "created", "created_at", "id"})
_CONTROL_FIELDS = ("chat_template_kwargs", "reasoning_effort", "reasoning")


def resolve_tier(ctx: CallContext, body_model: object, settings: Settings) -> Tier:
    """Header, else a tier literal (or a configured tier model id) in ``model``, else the
    configured agent tier."""
    if ctx.tier_header is not None:
        return ctx.tier_header
    if isinstance(body_model, str):
        literal = parse_tier(body_model)
        if literal is not None:
            return literal
        for tier in TIERS:
            if body_model == model_for(tier, settings):
                return tier
    return settings.ratchet_agent_tier


def _thinking_fields(
    ctx: CallContext, tier: Tier, settings: Settings, *, responses: bool
) -> dict[str, object]:
    thinking = ctx.thinking
    if thinking is None and ctx.purpose == "agent" and settings.ratchet_agent_thinking != "default":
        # Nemotron 3 thinks by default (docs/VERIFIED.md). The agent's setting is a
        # proxy policy, so golden and replay runs see the same behavior (ADR 0003).
        thinking = settings.ratchet_agent_thinking == "on"
    if thinking is None and ctx.reasoning_budget is None:
        return {}
    enabled = thinking if thinking is not None else True
    fields = thinking_params(tier, enabled, ctx.reasoning_budget, settings)
    if responses and "reasoning_effort" in fields:
        # The Responses API spells it reasoning.effort (docs/research/nebius-tavily.md A4).
        return {"reasoning": {"effort": fields["reasoning_effort"]}}
    return fields


def _pin_temperature(out: dict[str, object], ctx: CallContext) -> None:
    """Judge purposes: always 0. Agent: 0 only when the caller sent none."""
    if ctx.purpose != "agent":
        out["temperature"] = 0
    else:
        out.setdefault("temperature", 0)


def rewrite_chat(
    body: dict[str, object],
    *,
    ctx: CallContext,
    tier: Tier,
    seed: int | None,
    settings: Settings,
) -> dict[str, object]:
    out = dict(body)
    out["model"] = model_for(tier, settings)
    out = deep_merge(out, _thinking_fields(ctx, tier, settings, responses=False))
    if "seed" not in out and seed is not None:
        out["seed"] = seed
    _pin_temperature(out, ctx)
    if out.get("stream") is True:
        options = as_dict(out.get("stream_options"))
        options["include_usage"] = True
        out["stream_options"] = options
    return out


def rewrite_responses(
    body: dict[str, object], *, ctx: CallContext, tier: Tier, settings: Settings
) -> dict[str, object]:
    out = dict(body)
    out["model"] = model_for(tier, settings)
    out = deep_merge(out, _thinking_fields(ctx, tier, settings, responses=True))
    _pin_temperature(out, ctx)
    return out


def _strip_volatile(message: object) -> object:
    if not isinstance(message, dict):
        return message
    clean: dict[str, object] = {}
    for key, value in as_dict(message).items():
        if key in _VOLATILE_MESSAGE_KEYS:
            continue
        if key == "tool_calls":
            clean[key] = [_strip_volatile(call) for call in as_list(value)]
        else:
            clean[key] = value
    return clean


def request_hash(body: dict[str, object], *, api: str = "chat") -> str:
    """sha256 over the fields that decide the answer, volatile fields removed.

    Tool-call ids and timestamps differ between a golden and its replay without the request
    meaning anything different, so they never reach the hash. ``model`` is not hashed: the
    cache key adds it separately.
    """
    fields = HASHED_CHAT_FIELDS if api == "chat" else _HASHED_RESPONSES_FIELDS
    material: dict[str, object] = {}
    for name in fields:
        if name not in body:
            continue
        value = body[name]
        if name in {"messages", "input"} and isinstance(value, list):
            value = [_strip_volatile(m) for m in value]
        material[name] = value
    return hash_json(material)


def control_fields(body: dict[str, object]) -> dict[str, object]:
    """Reasoning controls; part of the response-cache key because they change the answer."""
    return {name: body[name] for name in _CONTROL_FIELDS if name in body}
