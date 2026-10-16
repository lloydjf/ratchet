"""Simulated ``changelog-writer`` policy: ``git.log`` → ``fs.read`` → ``fs.write`` → reply.

Directives, read from the skill text:

=================  ===================================================  =======================
directive          regex (case-insensitive)                             effect
=================  ===================================================  =======================
``groups``         ```<type>:` (commits) under `### <Heading>```        prefix → section
``default_group``  ``everything else under `### <Heading>```            unmatched commits
``skip``           ``skip commits whose message starts with `a` or `b``  dropped commits
``target``         first ```<NAME>.md``` in the skill                   changelog file
=================  ===================================================  =======================
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ratchet.sim.agent.base import Conversation, PlannedCall, Reply
from ratchet.sim.text import backticked

__all__ = ["NAME", "Directives", "directives", "section", "policy"]

NAME = "changelog-writer"
GROUP = re.compile(r"`(\w+):`\s+(?:commits\s+)?(?:go\s+)?under\s+`###\s+([^`]+)`", re.I)
DEFAULT_GROUP = re.compile(r"everything else under\s+`###\s+([^`]+)`", re.IGNORECASE)
SKIP = re.compile(r"skip commits whose message starts with ((?:`[^`]+`(?:,\s*|\s+or\s+)?)+)", re.I)
TARGET = re.compile(r"`([A-Za-z0-9_.-]+\.md)`")
REPO = re.compile(r"\b(acme/[a-z0-9][a-z0-9._-]*[a-z0-9])\b", re.IGNORECASE)
SINCE = re.compile(r"\bsince\s+(v?\d+\.\d+\.\d+|\d{4}-\d{2}-\d{2})", re.IGNORECASE)
VERSION = re.compile(r"\b(?:release|version)\s+(v?\d+\.\d+\.\d+)", re.IGNORECASE)
_CONVENTIONAL = re.compile(r"^(\w+)(?:\([^)]*\))?!?:\s*(.+)$")


@dataclass(frozen=True)
class Directives:
    groups: dict[str, str] = field(default_factory=lambda: {"feat": "Added", "fix": "Fixed"})
    default_group: str = "Changed"
    skip: tuple[str, ...] = ()
    target: str = "CHANGELOG.md"


def directives(body: str) -> Directives:
    groups = {prefix.lower(): heading.strip() for prefix, heading in GROUP.findall(body)}
    default, skip, target = DEFAULT_GROUP.search(body), SKIP.search(body), TARGET.search(body)
    return Directives(
        groups=groups or {"feat": "Added", "fix": "Fixed"},
        default_group=default.group(1).strip() if default else "Changed",
        skip=tuple(backticked(skip.group(1))) if skip else (),
        target=target.group(1) if target else "CHANGELOG.md",
    )


def _entry(message: str, sha: str) -> tuple[str | None, str]:
    first = message.strip().splitlines()[0] if message.strip() else ""
    match = _CONVENTIONAL.match(first)
    prefix, text = (match.group(1).lower(), match.group(2)) if match else (None, first)
    return prefix, f"{text[:1].upper()}{text[1:]} ({sha[:7]})"


def section(commits: list[dict[str, object]], version: str, day: str, d: Directives) -> str:
    order = [*dict.fromkeys(d.groups.values()), d.default_group]
    grouped: dict[str, list[str]] = {heading: [] for heading in order}
    for commit in commits:
        message = str(commit.get("message", ""))
        if any(message.startswith(s) for s in d.skip):
            continue
        prefix, entry = _entry(message, str(commit.get("sha", "")))
        grouped[d.groups.get(prefix or "", d.default_group)].append(entry)
    parts = [f"## {version} - {day}"]
    for heading in order:
        if grouped[heading]:
            parts.append(f"### {heading}\n" + "\n".join(f"- {e}" for e in grouped[heading]))
    return "\n\n".join(parts)


def _commits(conv: Conversation) -> list[dict[str, object]]:
    uses = conv.used("git.log")
    raw = uses[-1].result.get("commits") if uses else None
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def policy(conv: Conversation, body: str) -> Reply:
    repo = REPO.search(conv.task)
    if repo is None:
        return Reply("Which repository should the changelog cover? Please name it as acme/<name>.")
    d = directives(body)
    since, version = SINCE.search(conv.task), VERSION.search(conv.task)
    if not conv.called("git.log"):
        args: dict[str, object] = {"repo": repo.group(1).lower()}
        if since:
            args["since"] = since.group(1)
        return Reply(calls=[PlannedCall("git.log", args)])
    if not conv.called("fs.read"):
        return Reply(calls=[PlannedCall("fs.read", {"path": d.target})])
    commits = _commits(conv)
    label = version.group(1) if version else "Unreleased"
    new = section(commits, label, conv.now.date().isoformat(), d)
    entries = new.count("\n- ")
    if not conv.called("fs.write"):
        old = conv.used("fs.read")[-1]
        previous = "" if old.failed else str(old.result.get("content", "")).strip()
        content = f"# Changelog\n\n{new}\n" if not previous else _prepend(previous, new)
        return Reply(calls=[PlannedCall("fs.write", {"path": d.target, "content": content})])
    return Reply(f"Updated {d.target} with {entries} entries for {label}.")


def _prepend(previous: str, new: str) -> str:
    """The new section right under the file's ``# Title`` line (or on top)."""
    if previous.startswith("# "):
        title, _, rest = previous.partition("\n")
        return f"{title}\n\n{new}\n" + (f"\n{rest.strip()}\n" if rest.strip() else "")
    return f"{new}\n\n{previous}\n"
