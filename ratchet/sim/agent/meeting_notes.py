"""Simulated ``meeting-notes`` policy: ``fs.read`` the notes → ``fs.write`` minutes → reply.

Directives, read from the skill text:

================  ======================================================  ====================
directive         regex (case-insensitive)                                effect
================  ======================================================  ====================
``decisions``     ``decisions are the lines that start with `A:` or `B:```  decision prefixes
``actions``       ``action items are the lines that start with `A:` ...``   action prefixes
``attendees``     ``from the `<Key>:` line``                                attendee line key
``out_dir``       ``fs.write` to `<dir>/<``                                 minutes directory
================  ======================================================  ====================
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ratchet.sim.agent.base import Conversation, PlannedCall, Reply
from ratchet.sim.text import backticked

__all__ = ["NAME", "Directives", "directives", "minutes", "policy"]

NAME = "meeting-notes"
_PREFIXES = r"((?:`[^`]+`(?:,\s*|\s+or\s+)?)+)"
DECISIONS = re.compile(r"decisions are (?:the )?lines that start with " + _PREFIXES, re.I)
ACTIONS = re.compile(r"action items are (?:the )?lines that start with " + _PREFIXES, re.I)
ATTENDEES = re.compile(r"from the `([A-Za-z ]+):` line", re.IGNORECASE)
OUT_DIR = re.compile(r"`fs\.write`\s+to\s+`([^`<]+?)/?<", re.IGNORECASE)
NOTES_PATH = re.compile(r"\b((?:[\w.-]+/)*[\w.-]+\.(?:md|txt))\b")


@dataclass(frozen=True)
class Directives:
    decisions: tuple[str, ...] = ("Decision:",)
    actions: tuple[str, ...] = ("TODO:", "Action:")
    attendees: str = "Attendees"
    out_dir: str = "minutes"


def _prefixes(match: re.Match[str] | None, default: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(backticked(match.group(1))) if match else default


def directives(body: str) -> Directives:
    attendees, out_dir = ATTENDEES.search(body), OUT_DIR.search(body)
    return Directives(
        decisions=_prefixes(DECISIONS.search(body), ("Decision:",)),
        actions=_prefixes(ACTIONS.search(body), ("TODO:", "Action:")),
        attendees=attendees.group(1) if attendees else "Attendees",
        out_dir=out_dir.group(1).strip("/") if out_dir else "minutes",
    )


def _strip_prefix(line: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if line.lower().startswith(prefix.lower()):
            return line[len(prefix) :].strip()
    return None


def minutes(notes: str, path: str, d: Directives) -> tuple[str, int, int]:
    lines = [line.strip().lstrip("-* ").strip() for line in notes.splitlines()]
    heading = next((raw[2:].strip() for raw in notes.splitlines() if raw.startswith("# ")), None)
    title = heading or PurePosixPath(path).stem
    who = next((v for x in lines if (v := _strip_prefix(x, (f"{d.attendees}:",)))), None)
    decisions = [v for x in lines if (v := _strip_prefix(x, d.decisions))]
    actions = [v for x in lines if (v := _strip_prefix(x, d.actions))]
    parts = [f"# Minutes: {title}", "## Attendees", who or "Not recorded", "## Decisions"]
    parts.append("\n".join(f"- {x}" for x in decisions) or "None recorded")
    parts.append("## Action items")
    parts.append("\n".join(f"- [ ] {x}" for x in actions) or "None recorded")
    return "\n\n".join(parts) + "\n", len(decisions), len(actions)


def policy(conv: Conversation, body: str) -> Reply:
    found = NOTES_PATH.search(conv.task)
    if found is None:
        return Reply("Which notes file should I turn into minutes? Please give its path.")
    path = found.group(1)
    d = directives(body)
    reads = conv.used("fs.read")
    if not reads:
        return Reply(calls=[PlannedCall("fs.read", {"path": path})])
    if reads[-1].failed:
        return Reply(f"I could not read {path}, so no minutes were written.")
    out = f"{d.out_dir}/{PurePosixPath(path).name}"
    text, n_decisions, n_actions = minutes(str(reads[-1].result.get("content", "")), path, d)
    if not conv.called("fs.write"):
        return Reply(calls=[PlannedCall("fs.write", {"path": out, "content": text})])
    return Reply(f"Minutes written to {out}\nDecisions: {n_decisions}\nAction items: {n_actions}")
