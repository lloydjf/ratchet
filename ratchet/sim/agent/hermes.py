"""Hermes Agent's request shape for the simulator's skill policies (ADR 0018).

A Hermes v0.22 one-shot (``docs/research/hermes-live-s3.md``) differs from the MinimalRunner:

- **Skills.** The system prompt holds only an index (``<available_skills>`` with
  ``- <name>: <description cut to 60 chars>`` rows, grouped by category). The model loads a
  skill with the ``skill_view`` tool, whose result is JSON ``{"success", "name",
  "content": "<full SKILL.md>", …}``. With ``-s <name>`` the full SKILL.md is instead
  appended behind ``[IMPORTANT: The user launched this CLI session with the "<name>" skill
  preloaded. …]`` and ends before ``[Skill directory: …]``.
- **Tool names.** Mock tools are ``mcp__<server>__<mcp name>`` (``mcp__ratchet_mock__
  github_label``); the workspace tools are Hermes's ``read_file`` / ``write_file`` (read
  results carry a ``<n>|`` line gutter).
- **Tool results.** MCP text comes back as ``{"result": "<tool text>"}``, above a size
  threshold also wrapped in ``<untrusted_tool_result source="…">…</untrusted_tool_result>``;
  errors are ``{"error": "<text>"}``.
- **Date.** ``Conversation started: Friday, October 16, 2026 (UTC, …)``: date only (parsed by
  :mod:`ratchet.sim.agent.base`).

:func:`parse_conversation` turns such a request into the same :class:`Conversation` the
MinimalRunner shape gives, so the same skill policies answer it. :func:`first_turn` stands in
for the model's own trigger decision: with no skill loaded yet and ``skill_view`` offered, it
loads the best index row by the MinimalRunner's lexical matcher (threshold
:data:`INDEX_THRESHOLD`, lower than the MinimalRunner's because the index holds only 60
characters of each description; on the demo goldens it picks the same skill as the
MinimalRunner). :func:`in_dialect` makes a reply answer with Hermes's tool names.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from functools import partial
from pathlib import Path

from ratchet.replay.harness.skills import Skill, select_skills
from ratchet.sim.agent.base import Conversation, PlannedCall, Reply, SkillText
from ratchet.sim.agent.base import parse_conversation as parse_minimal
from ratchet.tools.catalog import MCP_PREFIX, from_mcp_name, from_wire_name, mcp_name, wire_name

__all__ = [
    "DEFAULT_SERVER",
    "INDEX_THRESHOLD",
    "SKILL_VIEW",
    "is_hermes",
    "parse_conversation",
    "first_turn",
    "in_dialect",
    "tool_name",
    "dotted_name",
    "unwrap_result",
]

DEFAULT_SERVER = "ratchet_mock"
SKILL_VIEW = "skill_view"
INDEX_THRESHOLD = 0.15
_BUILTINS = {"read_file": "fs.read", "write_file": "fs.write"}
_TO_BUILTIN = {dotted: name for name, dotted in _BUILTINS.items()}
_INDEX = re.compile(r"<available_skills>(.*?)</available_skills>", re.DOTALL)
_INDEX_ROW = re.compile(r"^\s*-\s*([a-z0-9][a-z0-9-]*)(?::[ \t]*(.*))?$", re.MULTILINE)
_PRELOAD = re.compile(
    r'\[IMPORTANT: The user launched this CLI session with the "([a-z0-9][a-z0-9-]*)" skill '
    r"preloaded\.[^\]]*\]\s*\n(.*?)(?=\n\[Skill directory:|\Z)",
    re.DOTALL,
)
_FRONTMATTER = re.compile(r"\A\s*---\n.*?\n---\n", re.DOTALL)
_SKILL_DIR = re.compile(r"\n\[Skill directory:.*\Z", re.DOTALL)
_UNTRUSTED = re.compile(
    r'\A<untrusted_tool_result source="[^"]*">\n[^\n]*\n\n(.*)\n</untrusted_tool_result>\s*\Z',
    re.DOTALL,
)
_GUTTER = re.compile(r"^\d+\|", re.MULTILINE)


def _names(tools: list[object] | None) -> list[str]:
    names = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


def _system(messages: list[dict[str, object]]) -> str:
    return "\n\n".join(
        str(m.get("content")) for m in messages if m.get("role") == "system" and m.get("content")
    )


def is_hermes(messages: list[dict[str, object]], tools: list[object] | None) -> bool:
    """A Hermes request offers ``skill_view`` or ``mcp__`` tools, or carries a skills index."""
    if any(n == SKILL_VIEW or n.startswith(MCP_PREFIX) for n in _names(tools)):
        return True
    return "<available_skills>" in _system(messages)


def _server(tools: list[object] | None) -> str:
    for name in _names(tools):
        if name.startswith(MCP_PREFIX):
            server, sep, _ = name[len(MCP_PREFIX) :].partition("__")
            if sep and server:
                return server
    return DEFAULT_SERVER


def tool_name(tool: str, server: str = DEFAULT_SERVER) -> str:
    """Dotted catalog name → Hermes's name (``github.label`` → ``mcp__<server>__github_label``)."""
    if tool in _TO_BUILTIN:
        return _TO_BUILTIN[tool]
    if tool == SKILL_VIEW:
        return tool
    if from_mcp_name(mcp_name(tool)) == tool:
        return f"{MCP_PREFIX}{server}__{mcp_name(tool)}"
    return wire_name(tool)


def dotted_name(name: str) -> str:
    """Hermes tool name → dotted catalog name (``skill_view`` stays as it is)."""
    return _BUILTINS.get(name) or from_wire_name(name)


def _strip_gutter(text: str) -> str:
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, dict) and isinstance(value.get("content"), str):
        value = {**value, "content": _GUTTER.sub("", value["content"])}
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text


def unwrap_result(text: str) -> str:
    """Tool message content → the tool's own text (drop the untrusted wrapper and the
    ``{"result": "<text>"}`` envelope)."""
    match = _UNTRUSTED.match(text)
    if match:
        text = match.group(1)
    try:
        value = json.loads(text)
    except ValueError:
        return text
    if isinstance(value, dict) and isinstance(value.get("result"), str) and "error" not in value:
        return str(value["result"])
    return text


def _normalized(message: dict[str, object], names: dict[str, str]) -> dict[str, object]:
    if message.get("role") == "tool":
        content = message.get("content")
        text = unwrap_result(content if isinstance(content, str) else "")
        if names.get(str(message.get("tool_call_id", ""))) == "fs.read":
            text = _strip_gutter(text)
        return {**message, "content": text}
    calls = message.get("tool_calls")
    if message.get("role") != "assistant" or not isinstance(calls, list):
        return message
    mapped = []
    for call in calls:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(call, dict) or not isinstance(function, dict):
            mapped.append(call)
            continue
        dotted = dotted_name(str(function.get("name", "")))
        mapped.append({**call, "function": {**function, "name": wire_name(dotted)}})
    return {**message, "tool_calls": mapped}


def _call_names(messages: list[dict[str, object]]) -> dict[str, str]:
    names: dict[str, str] = {}
    for message in messages:
        calls = message.get("tool_calls") if message.get("role") == "assistant" else None
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(call, dict) and isinstance(function, dict):
                names[str(call.get("id", ""))] = dotted_name(str(function.get("name", "")))
    return names


def _body(skill_md: str) -> str:
    return _SKILL_DIR.sub("", _FRONTMATTER.sub("", skill_md)).strip()


def _loaded(system: str, conv: Conversation) -> list[SkillText]:
    skills: list[SkillText] = []
    for name, text in _PRELOAD.findall(system):
        skills.append(SkillText(name, _body(text)))
    for use in conv.used(SKILL_VIEW):
        name, content = use.result.get("name"), use.result.get("content")
        if use.result.get("success") is False or not isinstance(content, str):
            continue
        skills.append(SkillText(str(name or use.args.get("name") or ""), _body(content)))
    unique: dict[str, SkillText] = {}
    for skill in skills:
        if skill.name:
            unique.setdefault(skill.name, skill)
    return list(unique.values())


def parse_conversation(messages: list[dict[str, object]]) -> Conversation:
    """A Hermes request as the policies' :class:`Conversation` (dotted tool names, decoded
    results, the loaded skills in order: a preloaded one first, then each ``skill_view``)."""
    names = _call_names(messages)
    conv = parse_minimal([_normalized(m, names) for m in messages])
    return replace(conv, skills=_loaded(conv.system, conv))


def _index(system: str) -> list[Skill]:
    match = _INDEX.search(system)
    rows = _INDEX_ROW.findall(match.group(1)) if match else []
    return [Skill(name, desc.strip(), "", Path(name)) for name, desc in rows]


def first_turn(conv: Conversation, tools: list[object] | None) -> Reply | None:
    """``skill_view(<best index row>)`` when no skill is loaded yet (else ``None``)."""
    if conv.skills or SKILL_VIEW not in _names(tools) or conv.called(SKILL_VIEW):
        return None
    best = select_skills(conv.task, _index(conv.system), threshold=INDEX_THRESHOLD, limit=1)
    if not best:
        return None
    return Reply(calls=[PlannedCall(SKILL_VIEW, {"name": best[0].name})])


def in_dialect(reply: Reply, tools: list[object] | None) -> Reply:
    """``reply`` answering with the tool names this Hermes request offered."""
    return replace(reply, namer=partial(tool_name, server=_server(tools)))
