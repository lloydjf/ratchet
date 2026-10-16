"""Every user-facing string, shared by the dashboard API, CLI, notifier and narrative.

Voice: sentences under 20 words, active voice, name the thing, no exclamation marks,
no emoji, costs always with their tier. ``dashboard/src/copy.ts`` mirrors these strings.
Templates are functions so a missing field is a type error, not a broken sentence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Final

from ratchet.copy_errors import (
    ERROR_COPY,
    NOT_READY_HOST,
    REASON_TEXT,
    SIMULATE_BUSY_CLIENT,
    error_copy,
    reason_text,
)
from ratchet.models import Tier

__all__ = [
    "TIER_PRODUCT_NAMES",
    "TIER_BADGES",
    "headline_block_level0",
    "headline_block_cascade",
    "headline_held_edit",
    "failure_phrase",
    "headline_warn",
    "headline_pass",
    "headline_pass_drift",
    "headline_no_traces",
    "DECISION_WORDS",
    "WARN_REASON_PHRASES",
    "ACTION_PHRASES",
    "action_phrase",
    "OUTCOME_LABELS",
    "explain_pass",
    "explain_regression",
    "explain_drift",
    "explain_improved",
    "explain_uncertain",
    "explain_failed",
    "explain_nondeterminism",
    "badge_tooltip",
    "SKIPPED_TIER",
    "level0_marker",
    "TRUNCATED_MARKER",
    "DRIFT_UNAVAILABLE_LABEL",
    "STAGE_KEYS",
    "stage_label",
    "retry_detail",
    "stalled_label",
    "rollback_confirm",
    "confirm_rollback_merged",
    "ROLLBACK_TOAST",
    "ROLLBACK_MERGED_TOAST",
    "approve_anyway_toast",
    "action_toast",
    "approve_confirm",
    "approve_toast",
    "rebless_confirm",
    "REBLESS_TOAST",
    "ERROR_COPY",
    "NOT_READY_HOST",
    "REASON_TEXT",
    "SIMULATE_BUSY_CLIENT",
    "error_copy",
    "reason_text",
    "EMPTY_TIMELINE",
    "EMPTY_VERDICTS",
    "EMPTY_TRACES",
    "EMPTY_COSTS",
    "CLOSING_NOTHING",
    "CLOSING_APPROVE",
    "CLOSING_ROLL_BACK",
    "CLOSING_REBLESS",
    "CLOSING_SENTENCES",
    "NARRATIVE_MAX_WORDS",
    "DIGEST_MAX_WORDS",
    "NOTIFICATION_MAX_CHARS",
    "notification",
    "cost_with_tier",
]

# --- tiers ------------------------------------------------------------------------------------
TIER_PRODUCT_NAMES: Final[dict[Tier, str]] = {
    "nano": "Nemotron Nano",
    "super": "Nemotron Super",
    "ultra": "Nemotron Ultra",
}
TIER_BADGES: Final[dict[Tier, str]] = {"nano": "Nano", "super": "Super", "ultra": "Ultra"}


def cost_with_tier(usd: float, tier: Tier) -> str:
    """Costs always with tier — ``$0.04 (nano)``."""
    return f"${usd:.2f} ({tier})" if usd >= 0.01 or usd == 0 else f"${usd:.4f} ({tier})"


# --- decision headlines -----------------------------------------------------------------------
DECISION_WORDS: Final[dict[str, str]] = {"BLOCK": "Blocked", "WARN": "Held", "PASS": "Passed"}


def headline_block_level0(skill: str, action: str, n: int, m: int) -> str:
    return f"Blocked: the new {skill} would {action} in {n} of {m} replays."


def headline_block_cascade(skill: str, failure: str, n: int, m: int) -> str:
    return f"Blocked: {skill} now {failure} on {n} of {m} past tasks."


# Criterion shapes produced by infer_success_criteria, mapped to plain phrasing
# ("now drops the VAT line"). Anything else is quoted verbatim, which is always truthful.
_INCLUDES = re.compile(
    r"^(?:the\s+)?(?:final\s+)?(?:output|report|summary|reply)\s+(?:includes|contains|lists|"
    r"shows|has)\s+(?P<thing>.+?)\.?$",
    re.IGNORECASE,
)
_CALLED = re.compile(r"^`?(?P<tool>[a-z][\w.]*)`?\s+is\s+called\.?$", re.IGNORECASE)
_STATES = re.compile(
    r"^(?:states|mentions|lists|reports|shows|gives|includes|contains)\s+(?P<thing>(?!that\b).+?)\.?$",
    re.IGNORECASE,
)


def failure_phrase(criterion: str) -> str:
    """How a failed success criterion reads inside the cascade BLOCK headline."""
    text = criterion.strip()
    if match := _INCLUDES.match(text):
        thing = match.group("thing").strip()
        if thing.lower().startswith(("the ", "a ", "an ")):
            return f"drops {thing[0].lower()}{thing[1:]}"
        return f"no longer includes {thing}"
    if match := _CALLED.match(text):
        return f"no longer calls {match.group('tool')}"
    if match := _STATES.match(text):
        return f"omits {match.group('thing').strip()}"
    return f'fails "{text.rstrip(".")}"'


def headline_held_edit(reason: str) -> str:
    """WARN for edit-level reasons that are not about any one replay (merge conflicts)."""
    return f"Held: {reason}. Review before merging."


def headline_warn(reason: str, n: int, m: int) -> str:
    return f"Held: {reason} on {n} of {m} replays. Review before merging."


def headline_pass(skill: str, m: int) -> str:
    return f"Passed: {skill} still handles all {m} past tasks."


def headline_pass_drift(m: int, n: int) -> str:
    return f"Passed: {m} tasks checked; {n} changed because the world did, not the skill."


def headline_no_traces(skill: str) -> str:
    return f"Passed: no blessed tasks use {skill} yet. Bless one to protect it."


# Reason phrases for the WARN template, keyed by verdict reason code.
WARN_REASON_PHRASES: Final[dict[str, str]] = {
    "judges_disagreed": "judges disagreed",
    "single_judge_regression": "one judge found a regression",
    "uncertain": "the judges were not sure",
    "judge_timeout": "the final judge timed out",
    "replay_infra": "replays failed",
    "replay_failed": "replays failed",
    "upstream_unavailable": "the model service did not respond",
    "budget_exceeded": "the spending cap was reached",
    "merge_conflict": "skill files conflict with main",
    "stacked_on_held": "an earlier held edit is still waiting on candidate",
    "drift_unavailable": "the world-change check was unavailable",
}

# Level 0 headline verbs: what the new skill "would" do.
ACTION_PHRASES: Final[dict[str, str]] = {
    "github.close_issue": "close issues",
    "github.merge_pr": "merge pull requests",
    "github.open_pr": "open pull requests",
    "github.comment": "comment on issues",
    "calendar.create": "create calendar events",
    "gmail.send": "send email",
    "slack.post": "post to Slack",
    "sms.send": "send text messages",
    "fs.delete": "delete files",
    "shell.run": "run destructive shell commands",
    "db.drop": "drop database tables",
    "payments.pay": "make payments",
}


def action_phrase(tool: str) -> str:
    return ACTION_PHRASES.get(tool, f"call {tool}")


# --- pair outcomes ----------------------------------------------------------------------------
OUTCOME_LABELS: Final[dict[str, str]] = {
    "pass": "Same result",
    "regression": "Got worse",
    "drift": "World changed",
    "improved": "Better",
    "uncertain": "Not sure",
    "failed": "Could not replay",
}


def explain_pass() -> str:
    return "Output meets the criteria; differences are cosmetic."


def explain_regression(criterion: str) -> str:
    return f"{criterion.rstrip('.')} is no longer met."


def explain_drift(fact: str) -> str:
    return f"{fact.rstrip('.')} changed since the original run; the new output follows from it."


def explain_improved(criterion: str) -> str:
    return f"Meets the criteria and improves {criterion.rstrip('.')}."


def explain_uncertain(reason: str) -> str:
    return f"{reason.rstrip('.')}. Held for a human."


def explain_failed(code: str) -> str:
    return reason_text(code)


def explain_nondeterminism() -> str:
    """ADR 0023: the control replay (the unchanged skill) also differed from the golden."""
    return (
        "The unchanged skill also produced a different result on this task, "
        "so the difference is nondeterminism, not the edit."
    )


# --- tier and judge copy ----------------------------------------------------------------------
def badge_tooltip(tier: Tier, verdict: str, confidence: float, first_reason: str) -> str:
    pct = round(confidence * 100)
    return f"{TIER_BADGES[tier]} judged {verdict} with {pct}% confidence. {first_reason}".strip()


SKIPPED_TIER: Final = "Not needed: decided earlier in the cascade."
TRUNCATED_MARKER: Final = "Some evidence was shortened to fit this judge's context."
DRIFT_UNAVAILABLE_LABEL: Final = "drift check unavailable"


def level0_marker(n: int, side_effect: str) -> str:
    return f"Checked without a model: {n} new {side_effect} call(s)."


# --- progress strip ---------------------------------------------------------------------------
STAGE_KEYS: Final[tuple[str, ...]] = (
    "detected",
    "selecting",
    "replaying",
    "level0",
    "nano",
    "super",
    "ultra",
    "drift",
    "controls",
    "summary",
    "done",
)


def stage_label(
    key: str, k: int | None = None, n: int | None = None, *, runner: str = "Nebius Jobs"
) -> str:
    """Progress strip labels, with counts when known."""
    count = f" ({n})" if n is not None else ""
    labels = {
        "detected": "Detected edit",
        "selecting": f"Choosing tasks{count}",
        "replaying": f"Replaying on {runner}"
        + (f" ({k}/{n})" if k is not None and n is not None else ""),
        "level0": "Checking actions",
        "nano": f"Nano screening{count}",
        "super": f"Super checking{count}",
        "ultra": f"Ultra deciding{count}",
        "controls": "Replaying controls"
        + (f" ({k}/{n})" if k is not None and n is not None else ""),
        "drift": "Asking Tavily" + (f" ({n} fact{'s' if n != 1 else ''})" if n is not None else ""),
        "summary": "Writing the summary",
        "narrative": "Writing the summary",  # pre-Doc-24-key alias
        "done": "Done",
    }
    try:
        return labels[key]
    except KeyError as exc:
        raise ValueError(f"unknown stage {key!r}") from exc


def retry_detail(n: int, *, done: bool = False) -> str:
    return f"{'retried' if done else 'retrying'} {n} failed replay{'' if n == 1 else 's'}"


def stalled_label(stage: str) -> str:
    return f"Still working — {stage} is taking longer than usual."


# --- actions and confirmations ----------------------------------------------------------------
def rollback_confirm(sha: str) -> str:
    return (
        f"This reverts commit {sha} on candidate and leaves main unchanged. "
        "You can re-apply it later from the edit page."
    )


def confirm_rollback_merged(sha: str) -> str:
    """Rollback confirm when the edit is merged (the verdict's ``action`` is
    ``merged``): the revert also takes the change off main."""
    return (
        f"This reverts commit {sha} on candidate and removes the change from main. "
        "You can re-apply it later with Approve anyway."
    )


ROLLBACK_TOAST: Final = "Rolled back. main is unchanged."
ROLLBACK_MERGED_TOAST: Final = "Rolled back. main no longer has this edit."


def approve_confirm(skill: str) -> str:
    return (
        "This merges candidate into main. "
        f"Your agent will use the new {skill} on its next task. Rollback stays available."
    )


def approve_toast(skill: str) -> str:
    return f"Merged. {skill} is live."


def approve_anyway_toast(skill: str) -> str:
    """Approve on a reverted verdict (BLOCK or rolled back): the change is re-applied."""
    return f"Approved anyway. {skill} is re-applied and live on main."


def action_toast(which: str, prior_action: str, skills: Sequence[str]) -> str:
    """The toast for Rollback (``which="rollback"``) or Approve on a verdict whose ``action``
    was ``prior_action`` before it; the event payload and the API response carry the same."""
    if which == "rollback":
        return ROLLBACK_MERGED_TOAST if prior_action == "merged" else ROLLBACK_TOAST
    skill = ", ".join(skills) or "the skill"
    return approve_anyway_toast(skill) if prior_action == "reverted" else approve_toast(skill)


def rebless_confirm(task: str) -> str:
    return (
        f'This makes the replay the new reference for "{task}" and archives the old one '
        'with the reason "world changed".'
    )


REBLESS_TOAST: Final = "Re-blessed. Future checks compare against today's result."

# --- empty states -----------------------------------------------------------------------------
EMPTY_TIMELINE: Final = "Nothing here yet. Bless a finished task or simulate a scenario."
EMPTY_VERDICTS: Final = (
    "No skill edits checked yet. When your agent changes a skill, the verdict appears here."
)
EMPTY_TRACES: Final = "No blessed tasks. Run a task, then press Bless on it."
EMPTY_COSTS: Final = "No spend yet."

# --- narrative --------------------------------------------------------------------------------
CLOSING_NOTHING: Final = "Nothing to do."
CLOSING_APPROVE: Final = "Approve if this is intended."
CLOSING_ROLL_BACK: Final = "Roll back if this is not intended."
CLOSING_REBLESS: Final = "Re-bless the changed tasks."
CLOSING_SENTENCES: Final[tuple[str, ...]] = (
    CLOSING_NOTHING,
    CLOSING_APPROVE,
    CLOSING_ROLL_BACK,
    CLOSING_REBLESS,
)
NARRATIVE_MAX_WORDS: Final = 180
DIGEST_MAX_WORDS: Final = 250

# --- notifications ----------------------------------------------------------------------------
NOTIFICATION_MAX_CHARS: Final = 160


def notification(decision: str, headline: str, url: str) -> str:
    """``Ratchet {decision}: {headline} — {url}``, ≤ 160 chars; the headline is ellipsized."""
    prefix = f"Ratchet {decision}: "
    suffix = f" — {url}"
    room = NOTIFICATION_MAX_CHARS - len(prefix) - len(suffix)
    if room <= 1:
        return (prefix + suffix.lstrip())[:NOTIFICATION_MAX_CHARS]
    if len(headline) > room:
        headline = headline[: room - 1].rstrip() + "…"
    return f"{prefix}{headline}{suffix}"
