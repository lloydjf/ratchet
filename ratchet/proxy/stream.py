"""SSE stream reassembly.

The proxy forwards every chunk unchanged and feeds the same bytes here. The assembler
splits Server-Sent Events across arbitrary chunk boundaries and accumulates content,
``reasoning_content``, tool-call fragments by index and the final usage block, then
renders one non-streaming-shaped response so the trace store has a single shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ratchet.proxy.jsonx import as_dict, as_int, as_list, as_str

__all__ = [
    "SseParser",
    "ChatStreamAssembler",
    "ResponsesStreamAssembler",
    "estimate_tokens",
    "estimate_prompt_tokens",
]


def estimate_tokens(text: str) -> int:
    """Approximate token count (~4 characters per token) when usage is missing."""
    return (len(text) + 3) // 4 if text else 0


def estimate_prompt_tokens(body: dict[str, object]) -> int:
    material = body.get("messages", body.get("input", ""))
    text = material if isinstance(material, str) else json.dumps(material, default=str)
    return estimate_tokens(text)


class SseParser:
    """Incremental ``text/event-stream`` parser yielding each event's ``data`` payload."""

    def __init__(self) -> None:
        self._buffer = b""
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer += chunk
        events: list[str] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                break
            raw, self._buffer = self._buffer[:newline], self._buffer[newline + 1 :]
            line = raw.rstrip(b"\r").decode("utf-8", errors="replace")
            if line == "":
                if self._data:
                    events.append("\n".join(self._data))
                    self._data = []
            elif line.startswith("data:"):
                self._data.append(line[6:] if line[5:6] == " " else line[5:])
        return events

    def close(self) -> list[str]:
        events = self.feed(b"\n\n") if self._buffer or self._data else []
        return events


@dataclass
class _ToolBuffer:
    id: str = ""
    type: str = "function"
    name: str = ""
    arguments: str = ""


@dataclass
class _ChoiceBuffer:
    role: str = "assistant"
    content: str = ""
    reasoning: str = ""
    finish_reason: object = None
    tools: dict[int, _ToolBuffer] = field(default_factory=dict)

    def message(self) -> dict[str, object]:
        message: dict[str, object] = {"role": self.role, "content": self.content}
        if self.reasoning:
            message["reasoning_content"] = self.reasoning
        if self.tools:
            message["tool_calls"] = [
                {
                    "id": buf.id,
                    "type": buf.type,
                    "function": {"name": buf.name, "arguments": buf.arguments},
                }
                for _, buf in sorted(self.tools.items())
            ]
        return message


class ChatStreamAssembler:
    """Accumulates ``chat.completion.chunk`` events into one ``chat.completion``."""

    def __init__(self) -> None:
        self._parser = SseParser()
        self._choices: dict[int, _ChoiceBuffer] = {}
        self._meta: dict[str, object] = {}
        self.usage: dict[str, object] | None = None
        self.done = False
        self.n_events = 0

    def feed(self, chunk: bytes) -> None:
        for data in self._parser.feed(chunk):
            self._event(data)

    def close(self) -> None:
        for data in self._parser.close():
            self._event(data)

    def _event(self, data: str) -> None:
        if data.strip() == "[DONE]":
            self.done = True
            return
        try:
            payload = as_dict(json.loads(data))
        except ValueError:
            return
        self.n_events += 1
        for key in ("id", "created", "model", "system_fingerprint"):
            if key in payload and key not in self._meta:
                self._meta[key] = payload[key]
        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            self.usage = as_dict(usage)
        for raw in as_list(payload.get("choices")):
            self._choice(as_dict(raw))

    def _choice(self, choice: dict[str, object]) -> None:
        buf = self._choices.setdefault(as_int(choice.get("index")), _ChoiceBuffer())
        if choice.get("finish_reason") is not None:
            buf.finish_reason = choice["finish_reason"]
        delta = as_dict(choice.get("delta"))
        if isinstance(delta.get("role"), str):
            buf.role = as_str(delta["role"])
        buf.content += as_str(delta.get("content"))
        buf.reasoning += as_str(delta.get("reasoning_content")) or as_str(delta.get("reasoning"))
        for raw in as_list(delta.get("tool_calls")):
            self._tool_fragment(buf, as_dict(raw))

    @staticmethod
    def _tool_fragment(buf: _ChoiceBuffer, fragment: dict[str, object]) -> None:
        index = as_int(fragment.get("index"), default=len(buf.tools))
        tool = buf.tools.setdefault(index, _ToolBuffer())
        if as_str(fragment.get("id")):
            tool.id = as_str(fragment["id"])
        if as_str(fragment.get("type")):
            tool.type = as_str(fragment["type"])
        function = as_dict(fragment.get("function"))
        tool.name += as_str(function.get("name"))
        tool.arguments += as_str(function.get("arguments"))

    @property
    def text(self) -> str:
        return self._choices[0].content if 0 in self._choices else ""

    def response(self) -> dict[str, object]:
        """The reassembled response in non-streaming shape."""
        choices: list[object] = [
            {"index": index, "message": buf.message(), "finish_reason": buf.finish_reason}
            for index, buf in sorted(self._choices.items())
        ]
        out: dict[str, object] = {"object": "chat.completion", **self._meta, "choices": choices}
        if self.usage is not None:
            out["usage"] = self.usage
        return out


class ResponsesStreamAssembler:
    """Responses API streams carry the full response on ``response.completed``."""

    def __init__(self) -> None:
        self._parser = SseParser()
        self._final: dict[str, object] | None = None
        self._text = ""
        self.done = False

    def feed(self, chunk: bytes) -> None:
        for data in self._parser.feed(chunk):
            self._event(data)

    def close(self) -> None:
        for data in self._parser.close():
            self._event(data)

    def _event(self, data: str) -> None:
        if data.strip() == "[DONE]":
            self.done = True
            return
        try:
            payload = as_dict(json.loads(data))
        except ValueError:
            return
        kind = as_str(payload.get("type"))
        if kind == "response.output_text.delta":
            self._text += as_str(payload.get("delta"))
        elif kind in {"response.completed", "response.incomplete", "response.failed"}:
            self._final = as_dict(payload.get("response"))
            self.done = True

    def response(self) -> dict[str, object]:
        if self._final is not None:
            return self._final
        message = {"type": "message", "content": [{"type": "output_text", "text": self._text}]}
        return {"object": "response", "output": [message]}
