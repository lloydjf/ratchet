"""Simulated ``github-triage`` policy: list → label (+ comment on questions) → [close] → summary.

Directives, read from the skill text (the skill text is the only source of behavior):

=====================  ===============================================================  =====
directive              regex (case-insensitive)                                         effect
=====================  ===============================================================  =====
``label_rules``        ``- `<label>`: the text mentions <kw>, <kw> or <kw>.``           first
                       matching rule (in order) labels the issue
``fallback_label``     ``If no rule matches, use `<label>```                            label
                       when no rule matches
``skip_labeled``       ``Skip every issue that already has one of the labels``          skip
                       issues carrying a rule label
``comment``            ``labeled `<label>` ... github.comment ... body: "<text>"``      post
                       ``<text>`` on issues given ``<label>``
``close_stale_days``   ``close ... (no activity|stale|inactive) ... <N> days``          close
                       issues whose ``updated_at`` is older than N days before "Current
                       date" (one sentence; reason from ``as `not_planned```)
=====================  ===============================================================  =====
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ratchet.sim.agent.base import Conversation, PlannedCall, Reply

__all__ = ["NAME", "Directives", "directives", "label_for", "stale", "policy"]

NAME = "github-triage"
LABEL_RULE = re.compile(
    r"^\s*[-*]\s*`([a-z][a-z0-9 _-]*)`:\s*the text mentions\s+([^\n]+?)\.?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
FALLBACK = re.compile(r"if no rule matches,\s*use\s*`([a-z][a-z0-9 _-]*)`", re.IGNORECASE)
SKIP_LABELED = re.compile(r"skip (?:every|any|each) issue that already has", re.IGNORECASE)
COMMENT = re.compile(
    r"labeled\s*`([a-z][a-z0-9 _-]*)`[^\n]*?github\.comment`?[^\n]*?body:\s*\"([^\"]+)\"",
    re.IGNORECASE,
)
CLOSE_STALE = re.compile(
    r"\bclose\b[^.\n]*?\b(?:no activity|stale|inactive)\b[^.\n]*?\b(\d+)\s+days", re.IGNORECASE
)
CLOSE_REASON = re.compile(r"as\s*`(not_planned|completed)`", re.IGNORECASE)
REPO = re.compile(r"\b(acme/[a-z0-9][a-z0-9._-]*[a-z0-9])\b", re.IGNORECASE)
_SPLIT = re.compile(r"\s*,\s*(?:or\s+)?|\s+or\s+")


@dataclass(frozen=True)
class Directives:
    rules: list[tuple[str, list[str]]] = field(default_factory=list)
    fallback: str = "question"
    skip_labeled: bool = False
    comments: dict[str, str] = field(default_factory=dict)
    close_stale_days: int | None = None
    close_reason: str = "not_planned"


def directives(body: str) -> Directives:
    rules = [
        (label.strip(), [k.strip().lower() for k in _SPLIT.split(kws) if k.strip()])
        for label, kws in LABEL_RULE.findall(body)
    ]
    fallback = FALLBACK.search(body)
    close = CLOSE_STALE.search(body)
    reason = CLOSE_REASON.search(close.group(0)) if close else None
    if close and reason is None:
        sentence_end = body.find(".", close.end())
        reason = CLOSE_REASON.search(body[close.start() : sentence_end + 1])
    return Directives(
        rules=rules,
        fallback=fallback.group(1) if fallback else "question",
        skip_labeled=bool(SKIP_LABELED.search(body)),
        comments={label: text for label, text in COMMENT.findall(body)},
        close_stale_days=int(close.group(1)) if close else None,
        close_reason=reason.group(1) if reason else "not_planned",
    )


def label_for(issue: dict[str, object], rules: list[tuple[str, list[str]]], fallback: str) -> str:
    text = f"{issue.get('title', '')}\n{issue.get('body', '')}".lower()
    for label, keywords in rules:
        if any(re.search(rf"\b{re.escape(k)}\b", text) for k in keywords):
            return label
    return fallback


def stale(issue: dict[str, object], now: datetime, days: int) -> bool:
    raw = str(issue.get("updated_at") or "")
    try:
        updated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    return updated < now - timedelta(days=days)


def _issues(conv: Conversation) -> list[dict[str, object]]:
    listed = conv.used("github.list_issues")
    raw = listed[-1].result.get("issues") if listed else None
    return [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []


def _plan(conv: Conversation, d: Directives) -> tuple[dict[int, str], list[int], list[int]]:
    known = {label for label, _ in d.rules} | {d.fallback}
    labels: dict[int, str] = {}
    already: list[int] = []
    for issue in _issues(conv):
        number = int(str(issue.get("number", 0)))
        current = issue.get("labels")
        has = set(current) if isinstance(current, list) else set()
        if d.skip_labeled and has & known:
            already.append(number)
            continue
        labels[number] = label_for(issue, d.rules, d.fallback)
    close: list[int] = []
    if d.close_stale_days is not None:
        close = [
            int(str(i.get("number", 0)))
            for i in _issues(conv)
            if stale(i, conv.now, d.close_stale_days)
        ]
    return labels, already, close


def _numbers(items: list[int]) -> str:
    return ", ".join(f"#{n}" for n in items) if items else "none"


def _summary(conv: Conversation, repo: str, d: Directives) -> str:
    labels, already, _ = _plan(conv, d)
    lines = [f"Triaged {repo}: {len(_issues(conv))} open issues."]
    lines += [f"#{n}: {label}" for n, label in sorted(labels.items())]
    lines.append(f"Already labeled: {_numbers(sorted(already))}")
    asked = sorted(n for n, label in labels.items() if label in d.comments)
    lines.append(f"Asked for details: {_numbers(asked)}")
    closes = conv.used("github.close_issue")
    done = [int(str(u.args.get("issue"))) for u in closes if not u.failed]
    refused = [int(str(u.args.get("issue"))) for u in closes if u.failed]
    if done:
        lines.append(f"Closed as {d.close_reason.replace('_', ' ')}: {_numbers(done)}")
    if refused:
        lines.append(f"Could not close: {_numbers(refused)} (the action was blocked)")
    return "\n".join(lines)


def policy(conv: Conversation, body: str) -> Reply:
    match = REPO.search(conv.task)
    if match is None:
        return Reply("Which repository should I triage? Please name it as acme/<name>.")
    repo = match.group(1).lower()
    d = directives(body)
    if not conv.called("github.list_issues"):
        return Reply(calls=[PlannedCall("github.list_issues", {"repo": repo, "state": "open"})])
    labels, _, close = _plan(conv, d)
    if labels and not conv.called("github.label"):
        calls = [
            PlannedCall("github.label", {"repo": repo, "issue": n, "labels": [label]})
            for n, label in sorted(labels.items())
        ]
        calls += [
            PlannedCall("github.comment", {"repo": repo, "issue": n, "body": d.comments[label]})
            for n, label in sorted(labels.items())
            if label in d.comments
        ]
        return Reply(calls=calls)
    if close and not conv.called("github.close_issue"):
        return Reply(
            calls=[
                PlannedCall(
                    "github.close_issue", {"repo": repo, "issue": n, "reason": d.close_reason}
                )
                for n in close
            ]
        )
    return Reply(_summary(conv, repo, d))
