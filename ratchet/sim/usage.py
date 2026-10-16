"""Token accounting for simulated responses: about 4 characters per token (ADR 0018).

Counts include the tool schemas and a small per-message overhead, the way a chat template
would, so the cost panels and the cascade-savings counterfactual stay meaningful offline.
These are estimates of what a real model would bill, not measurements.

Prompt caching is simulated without state (same request → same bytes): a request that
continues a conversation (it carries an assistant turn) re-sends the prefix its previous
turn already sent — the tool schemas and every message before the last assistant turn — so
those tokens are reported as ``prompt_cache_hit_tokens`` the way Token Factory reports them
(docs/VERIFIED.md "Prompt caching"). A first turn hits nothing.
"""

from __future__ import annotations

import json
import math

__all__ = [
    "CHARS_PER_TOKEN",
    "MESSAGE_OVERHEAD",
    "count",
    "prompt_tokens",
    "cached_prompt_tokens",
    "usage_block",
]

CHARS_PER_TOKEN = 4
MESSAGE_OVERHEAD = 4  # role markers and separators per message in a chat template


def count(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN) if text else 0


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _message_tokens(message: object) -> int:
    total = MESSAGE_OVERHEAD
    if isinstance(message, dict):
        total += count(_text(message.get("content") or ""))
        if message.get("tool_calls"):
            total += count(_text(message["tool_calls"]))
    return total


def _tools_tokens(body: dict[str, object]) -> int:
    tools = body.get("tools")
    return count(_text(tools)) if tools else 0


def prompt_tokens(body: dict[str, object]) -> int:
    messages = body.get("messages")
    total = 0
    if isinstance(messages, list):
        total += sum(_message_tokens(m) for m in messages)
    total += _tools_tokens(body)
    fmt = body.get("response_format")
    if isinstance(fmt, dict) and fmt.get("type") == "json_schema":
        total += count(_text(fmt.get("json_schema") or {}))
    return max(total, 1)


def cached_prompt_tokens(body: dict[str, object]) -> int:
    """The prefix a previous turn already sent: tools + messages before the last assistant
    turn; 0 for a first turn (no assistant message)."""
    messages = body.get("messages")
    if not isinstance(messages, list):
        return 0
    last = max(
        (i for i, m in enumerate(messages) if isinstance(m, dict) and m.get("role") == "assistant"),
        default=-1,
    )
    if last < 0:
        return 0
    return _tools_tokens(body) + sum(_message_tokens(m) for m in messages[:last])


def usage_block(
    prompt: int, completion: int, reasoning: int = 0, cached: int = 0
) -> dict[str, object]:
    hits = max(0, min(cached, prompt))
    block: dict[str, object] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "prompt_cache_hit_tokens": hits,
        "prompt_cache_miss_tokens": prompt - hits,
    }
    if reasoning:
        block["completion_tokens_details"] = {"reasoning_tokens": reasoning}
    return block
