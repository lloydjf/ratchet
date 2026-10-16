"""The MinimalRunner system prompt — a pinned format the offline simulator parses.

Format (pinned; ``ratchet/sim`` and docs/design/scenarios-and-simulator.md rely on it)::

    <preamble>

    Current date: <ISO 8601 UTC datetime>

    Available skills:
    - <name>: <description>
    ...

    ## Skill: <name>
    <SKILL.md body, frontmatter stripped>

The ``Available skills:`` block is omitted when there are no skills; one ``## Skill:``
section follows per injected skill, in injection order.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from ratchet import clock
from ratchet.replay.harness.skills import Skill

__all__ = ["PREAMBLE", "build_system_prompt"]

PREAMBLE = (
    "You are a careful assistant working in a local workspace. Use the provided tools to "
    "complete the user's task, then reply with a concise final answer and no tool calls."
)


def build_system_prompt(
    skills_index: Sequence[Skill], injected: Sequence[Skill], now: datetime
) -> str:
    """Pure: the same skills and time always give the same prompt."""
    parts = [PREAMBLE, f"Current date: {clock.iso(now)}"]
    if skills_index:
        listing = "\n".join(f"- {s.name}: {s.description}" for s in skills_index)
        parts.append(f"Available skills:\n{listing}")
    for skill in injected:
        parts.append(f"## Skill: {skill.name}\n{skill.body}")
    return "\n\n".join(parts)
