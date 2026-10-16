"""Recording a model call: step payloads, the ``LLMCall`` step and the ``costs`` row.

Request and response bodies are written verbatim apart from credentials: headers are never
stored, auth-looking body fields are dropped and any configured secret value is replaced by
``<SECRET>`` wherever it appears.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from ratchet.config.pricing import cost_usd
from ratchet.models import LLMCall, Tier
from ratchet.proxy.headers import CallContext
from ratchet.proxy.jsonx import as_dict, as_int, as_list, as_str
from ratchet.proxy.stream import estimate_prompt_tokens, estimate_tokens
from ratchet.store import CostRow, Store
from ratchet.tools.catalog import from_wire_name
from ratchet.util import preview

__all__ = [
    "Usage",
    "CallRecord",
    "usage_from",
    "cached_prompt_tokens",
    "assistant_text",
    "requested_tools",
    "scrub",
    "record_call",
    "SECRET_TOKEN",
]

SECRET_TOKEN = "<SECRET>"
_AUTH_KEYS = frozenset({"api_key", "apikey", "authorization", "x-api-key", "password"})


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    estimated: bool = False
    cached_prompt_tokens: int = 0  # served from the upstream's prompt (prefix) cache


@dataclass(frozen=True)
class CallRecord:
    ctx: CallContext
    step_id: str
    tier: Tier
    model: str
    api: str  # "chat" | "responses"
    request_body: dict[str, object]
    response_body: dict[str, object]
    request_hash: str
    usage: Usage
    latency_ms: int
    started_at: datetime
    cached: bool = False

    @property
    def usd(self) -> float:
        if self.cached:
            return 0.0
        return cost_usd(self.tier, self.usage.prompt_tokens, self.usage.completion_tokens)


def assistant_text(response: dict[str, object], api: str) -> str:
    if api == "chat":
        first = as_dict((as_list(response.get("choices")) or [{}])[0])
        return as_str(as_dict(first.get("message")).get("content"))
    if isinstance(response.get("output_text"), str):
        return as_str(response["output_text"])
    parts: list[str] = []
    for item in as_list(response.get("output")):
        for content in as_list(as_dict(item).get("content")):
            parts.append(as_str(as_dict(content).get("text")))
    return "".join(parts)


def requested_tools(response: dict[str, object], api: str) -> list[str]:
    """Dotted tool names the model asked for (``github__close_issue`` → ``github.close_issue``)."""
    names: list[str] = []
    if api == "chat":
        for choice in as_list(response.get("choices")):
            message = as_dict(as_dict(choice).get("message"))
            for call in as_list(message.get("tool_calls")):
                name = as_str(as_dict(as_dict(call).get("function")).get("name"))
                if name:
                    names.append(from_wire_name(name))
        return names
    for item in as_list(response.get("output")):
        entry = as_dict(item)
        if entry.get("type") == "function_call" and as_str(entry.get("name")):
            names.append(from_wire_name(as_str(entry["name"])))
    return names


def cached_prompt_tokens(usage: dict[str, object]) -> int:
    """Prompt-cache hits from a usage block; the first field present wins, 0 when none is.

    Token Factory reports ``prompt_cache_hit_tokens`` (docs/VERIFIED.md "Prompt caching");
    OpenAI-style servers ``prompt_tokens_details.cached_tokens`` (``input_tokens_details`` on
    ``/v1/responses``)."""
    if "prompt_cache_hit_tokens" in usage:
        return max(as_int(usage.get("prompt_cache_hit_tokens")), 0)
    for key in ("prompt_tokens_details", "input_tokens_details"):
        details = as_dict(usage.get(key))
        if "cached_tokens" in details:
            return max(as_int(details.get("cached_tokens")), 0)
    return 0


def usage_from(response: dict[str, object], request_body: dict[str, object], api: str) -> Usage:
    """Usage from the response; approximated (``estimated=True``) when the block is absent."""
    usage = as_dict(response.get("usage"))
    in_key, out_key = (
        ("prompt_tokens", "completion_tokens")
        if api == "chat"
        else ("input_tokens", "output_tokens")
    )
    if in_key in usage or out_key in usage:
        prompt = as_int(usage.get(in_key))
        cached = min(cached_prompt_tokens(usage), prompt)
        return Usage(prompt, as_int(usage.get(out_key)), cached_prompt_tokens=cached)
    completion = assistant_text(response, api)
    for name in requested_tools(response, api):
        completion += name
    return Usage(
        estimate_prompt_tokens(request_body), max(estimate_tokens(completion), 1), estimated=True
    )


def scrub(value: object, secrets: tuple[str, ...]) -> object:
    """Drop auth-looking keys and replace secret values anywhere in a JSON value."""
    if isinstance(value, dict):
        return {
            str(k): scrub(v, secrets) for k, v in value.items() if str(k).lower() not in _AUTH_KEYS
        }
    if isinstance(value, list):
        return [scrub(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                value = value.replace(secret, SECRET_TOKEN)
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def record_call(store: Store, rec: CallRecord, secrets: tuple[str, ...]) -> LLMCall:
    """Write payloads, append the step and the cost row (sync; run via ``asyncio.to_thread``)."""
    run_id = rec.ctx.run_id
    req_ref = store.write_step_payload(run_id, rec.step_id, "req", scrub(rec.request_body, secrets))
    res_ref = store.write_step_payload(
        run_id, rec.step_id, "res", scrub(rec.response_body, secrets)
    )
    text = assistant_text(rec.response_body, rec.api)
    step = LLMCall(
        step_id=rec.step_id,
        tier=rec.tier,
        model=rec.model,
        request_hash=rec.request_hash,
        messages_ref=req_ref,
        response_ref=res_ref,
        prompt_tokens=rec.usage.prompt_tokens,
        completion_tokens=rec.usage.completion_tokens,
        usd=rec.usd,
        latency_ms=rec.latency_ms,
        started_at=rec.started_at,
        purpose=rec.ctx.purpose,
        api="responses" if rec.api == "responses" else "chat",
        usage_estimated=rec.usage.estimated,
        tool_calls_requested=requested_tools(rec.response_body, rec.api),
        content_preview=preview(scrub(text, secrets), 400),
        cached_prompt_tokens=0 if rec.cached else rec.usage.cached_prompt_tokens,
    )
    store.append_step(run_id, step)
    store.record_cost(
        CostRow(
            call_id=f"{run_id}:{rec.step_id}",
            run_id=run_id,
            edit_id=rec.ctx.edit_id,
            tier=rec.tier,
            model=rec.model,
            # A cache hit spent nothing; zero tokens keep the counterfactual honest.
            prompt_tokens=0 if rec.cached else rec.usage.prompt_tokens,
            completion_tokens=0 if rec.cached else rec.usage.completion_tokens,
            usd=rec.usd,
            purpose=rec.ctx.purpose,
            cached=rec.cached,
            session_hash=rec.ctx.session_hash,
            cached_prompt_tokens=step.cached_prompt_tokens,
        )
    )
    return step
