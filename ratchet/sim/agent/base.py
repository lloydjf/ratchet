"""Conversation parsing and reply types for the simulator's agent policies (ADR 0018).

The MinimalRunner system prompt is pinned (``ratchet.replay.harness.prompt``)::

    Current date: <ISO 8601 UTC>
    Available skills:
    - <name>: <description>
    ## Skill: <name>
    <body>

A policy sees a :class:`Conversation`: the task (first user message), the current date, the
injected skills in order, and every tool call made so far with its result. It returns a
:class:`Reply`: tool calls to make next, or a final answer. Tool-call ids are deterministic
(``call_<n>``, counting every tool call in the conversation), tool names use the wire form
(``github__label``) unless the reply carries another ``namer`` (Hermes:
:mod:`ratchet.sim.agent.hermes`). The date is the ``Current date:`` line, else Hermes's
``Conversation started: <Weekday>, <Month> <D>, <YYYY>`` line (midnight UTC), else
:data:`DEFAULT_NOW`. Simulated replies are test infrastructure, never model output.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ratchet.tools.catalog import from_wire_name, wire_name

__all__ = [
    "SkillText",
    "ToolUse",
    "PlannedCall",
    "Reply",
    "Conversation",
    "parse_conversation",
    "DEFAULT_NOW",
]

DEFAULT_NOW = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # demo frozen time (no date line)
_CURRENT_DATE = re.compile(r"^Current date:\s*(\S+)\s*$", re.MULTILINE)
_STARTED = re.compile(r"^Conversation started:\s*[A-Za-z]+,\s*([A-Za-z]+ \d{1,2}, \d{4})", re.M)
_SKILL_HEADER = re.compile(r"^## Skill: ([a-z0-9][a-z0-9-]*)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class SkillText:
    name: str
    body: str


@dataclass(frozen=True)
class ToolUse:
    """One tool call already made, with its decoded result (``{}`` when not answered yet)."""

    call_id: str
    tool: str  # dotted name
    args: dict[str, object]
    result: dict[str, object]

    @property
    def failed(self) -> bool:
        return "error" in self.result


@dataclass(frozen=True)
class PlannedCall:
    tool: str  # dotted name
    args: dict[str, object]


@dataclass(frozen=True)
class Reply:
    content: str | None = None
    calls: list[PlannedCall] = field(default_factory=list)
    namer: Callable[[str], str] = field(default=wire_name, compare=False, repr=False)

    def tool_calls(self, already: int) -> list[dict[str, object]]:
        """OpenAI ``tool_calls`` with deterministic ids continuing after ``already`` calls."""
        return [
            {
                "id": f"call_{already + i + 1}",
                "type": "function",
                "function": {
                    "name": self.namer(call.tool),
                    "arguments": json.dumps(call.args, sort_keys=True, ensure_ascii=False),
                },
            }
            for i, call in enumerate(self.calls)
        ]


@dataclass(frozen=True)
class Conversation:
    system: str
    task: str
    now: datetime
    skills: list[SkillText]
    history: list[ToolUse]

    def used(self, tool: str) -> list[ToolUse]:
        return [use for use in self.history if use.tool == tool]

    def called(self, tool: str) -> bool:
        return any(use.tool == tool for use in self.history)

    @property
    def skill(self) -> SkillText | None:
        """The active skill: the first injected one (the matcher's best)."""
        return self.skills[0] if self.skills else None


def _content(message: dict[str, object]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content parts
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return ""


def _started(system: str) -> datetime:
    match = _STARTED.search(system)
    if match is None:
        return DEFAULT_NOW
    try:
        return datetime.strptime(match.group(1), "%B %d, %Y").replace(tzinfo=UTC)
    except ValueError:
        return DEFAULT_NOW


def _now(system: str) -> datetime:
    match = _CURRENT_DATE.search(system)
    if match is None:
        return _started(system)
    try:
        parsed = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return DEFAULT_NOW
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _skills(system: str) -> list[SkillText]:
    headers = list(_SKILL_HEADER.finditer(system))
    skills = []
    for i, match in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(system)
        skills.append(SkillText(match.group(1), system[match.end() : end].strip()))
    return skills


def _decode(text: str) -> dict[str, object]:
    try:
        value = json.loads(text)
    except ValueError:
        return {"text": text}
    return value if isinstance(value, dict) else {"result": value}


def _history(messages: list[dict[str, object]]) -> list[ToolUse]:
    results: dict[str, dict[str, object]] = {}
    ordered: list[dict[str, object]] = []
    for message in messages:
        if message.get("role") == "tool":
            decoded = _decode(_content(message))
            results[str(message.get("tool_call_id", ""))] = decoded
            ordered.append(decoded)
    uses: list[ToolUse] = []
    position = 0
    for message in messages:
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        for call in calls if isinstance(calls, list) else []:
            if not isinstance(call, dict):
                continue
            raw = call.get("function")
            function: dict[str, object] = raw if isinstance(raw, dict) else {}
            args = _decode(str(function.get("arguments") or "{}"))
            call_id = str(call.get("id", ""))
            result = results.get(call_id)
            if result is None:  # ids stripped: match results by order
                result = ordered[position] if position < len(ordered) else {}
            position += 1
            tool = from_wire_name(str(function.get("name", "")))
            uses.append(ToolUse(call_id, tool, args, result))
    return uses


def parse_conversation(messages: list[dict[str, object]]) -> Conversation:
    system = "\n\n".join(_content(m) for m in messages if m.get("role") == "system")
    task = next((_content(m) for m in messages if m.get("role") == "user"), "")
    return Conversation(system, task, _now(system), _skills(system), _history(messages))
