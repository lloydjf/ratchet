"""OpenAI-compatible response shaping for the simulator: completions and SSE streams.

Everything is a pure function of the request, so the same request always yields the same
bytes: ids are hashes of the canonical request body, ``created`` is a fixed epoch (the demo
frozen time), and JSON is serialized with sorted keys. Streams send a role chunk, content in
fixed-size pieces, tool-call fragments by index, a finish chunk, a usage chunk when
``stream_options.include_usage`` is set, then ``[DONE]``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field

from ratchet.sim.agent.base import DEFAULT_NOW
from ratchet.util import canonical_json, sha256_hex

__all__ = [
    "CREATED",
    "PIECE_CHARS",
    "Completion",
    "completion_id",
    "dumps",
    "payload",
    "sse_events",
]

CREATED = int(DEFAULT_NOW.timestamp())
PIECE_CHARS = 24
SYSTEM_FINGERPRINT = "ratchet-sim"


@dataclass(frozen=True)
class Completion:
    id: str
    model: str
    content: str | None
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    reasoning: str | None = None
    finish_reason: str = "stop"
    usage: dict[str, object] = field(default_factory=dict)


def completion_id(body: dict[str, object]) -> str:
    return "chatcmpl-sim-" + sha256_hex(canonical_json(body))[:24]


def dumps(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def payload(c: Completion) -> dict[str, object]:
    message: dict[str, object] = {"role": "assistant", "content": c.content}
    if c.tool_calls:
        message["tool_calls"] = c.tool_calls
    if c.reasoning:
        message["reasoning_content"] = c.reasoning
    return {
        "id": c.id,
        "object": "chat.completion",
        "created": CREATED,
        "model": c.model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [{"index": 0, "message": message, "finish_reason": c.finish_reason}],
        "usage": c.usage,
    }


def _pieces(text: str) -> list[str]:
    return [text[i : i + PIECE_CHARS] for i in range(0, len(text), PIECE_CHARS)]


def _chunk(c: Completion, delta: dict[str, object], finish: str | None = None) -> bytes:
    body = {
        "id": c.id,
        "object": "chat.completion.chunk",
        "created": CREATED,
        "model": c.model,
        "system_fingerprint": SYSTEM_FINGERPRINT,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + dumps(body) + b"\n\n"


def _tool_chunks(c: Completion) -> Iterator[bytes]:
    for index, call in enumerate(c.tool_calls):
        raw = call.get("function")
        function: dict[str, object] = raw if isinstance(raw, dict) else {}
        head = {
            "index": index,
            "id": call.get("id"),
            "type": "function",
            "function": {"name": function.get("name"), "arguments": ""},
        }
        yield _chunk(c, {"tool_calls": [head]})
        for piece in _pieces(str(function.get("arguments") or "")):
            yield _chunk(c, {"tool_calls": [{"index": index, "function": {"arguments": piece}}]})


def sse_events(c: Completion, *, include_usage: bool) -> Iterator[bytes]:
    yield _chunk(c, {"role": "assistant", "content": ""})
    for piece in _pieces(c.reasoning or ""):
        yield _chunk(c, {"reasoning_content": piece})
    for piece in _pieces(c.content or ""):
        yield _chunk(c, {"content": piece})
    yield from _tool_chunks(c)
    yield _chunk(c, {}, c.finish_reason)
    if include_usage:
        body = {
            "id": c.id,
            "object": "chat.completion.chunk",
            "created": CREATED,
            "model": c.model,
            "system_fingerprint": SYSTEM_FINGERPRINT,
            "choices": [],
            "usage": c.usage,
        }
        yield b"data: " + dumps(body) + b"\n\n"
    yield b"data: [DONE]\n\n"
