"""Agent-purpose dispatch for the simulator (design doc §4.1, ADR 0018).

The active skill is the first ``## Skill: <name>`` section in the system prompt (the
MinimalRunner injects the best match first). Its body is handed to that skill's policy,
which extracts its directives from the text and runs a state machine over the tool results
so far. No skill → the canary policy; a skill without a policy → a polite answer, no tools.
A Hermes Agent request (skills index, ``skill_view``, ``mcp__<server>__<tool>`` names;
:mod:`ratchet.sim.agent.hermes`) is mapped onto the same conversation, first loads the
matching skill with ``skill_view``, and is answered with Hermes's tool names.
Replies are scripted test infrastructure, never Nemotron output.
"""

from __future__ import annotations

from collections.abc import Callable

from ratchet.sim.agent import (
    canary,
    changelog,
    expense_report,
    fx_converter,
    github_triage,
    hermes,
    meeting_notes,
)
from ratchet.sim.agent.base import Conversation, Reply, parse_conversation

__all__ = ["POLICIES", "respond", "Reply", "Conversation", "parse_conversation"]

Policy = Callable[[Conversation, str], Reply]

POLICIES: dict[str, Policy] = {
    github_triage.NAME: github_triage.policy,
    expense_report.NAME: expense_report.policy,
    fx_converter.NAME: fx_converter.policy,
    meeting_notes.NAME: meeting_notes.policy,
    changelog.NAME: changelog.policy,
}


def _dispatch(conv: Conversation) -> Reply:
    skill = conv.skill
    if skill is None:
        return canary.policy(conv)
    policy = POLICIES.get(skill.name)
    if policy is None:
        return Reply(canary.UNKNOWN_REPLY)
    return policy(conv, skill.body)


def respond(
    messages: list[dict[str, object]], tools: list[object] | None = None
) -> tuple[Reply, int]:
    """The next reply and the number of tool calls already in the conversation.

    ``tools`` is the request's ``tools`` list; it tells a Hermes request apart and names the
    MCP server its replies must use."""
    if hermes.is_hermes(messages, tools):
        conv = hermes.parse_conversation(messages)
        reply = hermes.first_turn(conv, tools) or _dispatch(conv)
        return hermes.in_dialect(reply, tools), len(conv.history)
    conv = parse_conversation(messages)
    return _dispatch(conv), len(conv.history)
