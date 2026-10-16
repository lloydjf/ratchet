"""Error and reason copy per stable code, in the product voice.

Split out of ``ratchet.copy`` (which re-exports every name here) to keep modules under 400
lines. ``ERROR_COPY`` is the longer sentence for toasts and banners; ``REASON_TEXT`` the
short text for pair evidence, opinion reasons and badges. ``dashboard/src/copy.ts``
mirrors both; the registry of every code is in ``docs/CONTRACTS.md`` §9.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ERROR_COPY",
    "REASON_TEXT",
    "SIMULATE_BUSY_CLIENT",
    "NOT_READY_HOST",
    "error_copy",
    "reason_text",
]

# The API's 503 ``not_ready`` (a component the route needs is not wired yet). Same code as
# the 409 action refusal (``ERROR_COPY["not_ready"]``), different situation and text.
NOT_READY_HOST: Final[str] = "This host is not fully started yet; try again in a moment."

# Long copy (dialogs, banners). Fields in braces are filled by error_copy().
ERROR_COPY: Final[dict[str, str]] = {
    "upstream_unavailable": "The model service didn't respond. Ratchet judged this pair as not "
    "sure and will keep the edit held.",
    "judge_timeout": "The final judge timed out. The edit is held for you to review.",
    "replay_infra": "Replays failed on {n} of {m} tasks, so Ratchet can't decide. The edit is "
    "held. Try again from the edit page.",
    "drift_unavailable": "Couldn't check whether the world changed. Treating this difference as "
    "a possible regression.",
    "budget_exceeded": "Spending cap reached for today. The edit is held; raise "
    "RATCHET_DAILY_USD_CAP in .env or wait until tomorrow.",
    "merge_conflict": "The skill files conflict with main. Resolve the conflict in the skills "
    "repo, then approve.",
    "stacked_on_held": "An earlier edit on candidate is still held. Approve or roll it back "
    "first; merging this one would ship it too.",
    "not_public": "Blessing and recording are off on the demo. Run Ratchet locally to protect "
    "your own agent.",
    "rate_limited": "Too many actions in a minute. Wait a moment and try again.",
    "worker_lost": "The replay worker stopped before this task ran. The edit is held. Try again "
    "from the edit page.",
    "simulate_disabled": "Simulate is off on this host. Recorded runs are on the timeline.",
    "simulate_busy": "Many judges are running scenarios right now. Try again in a minute, or "
    "view a recorded run.",
    "action_token_invalid": "This page is out of date. Reload and try again.",
    "already_reverted": "This edit is already reverted; main is unchanged.",
    "not_ready": "Ratchet is still acting on this verdict. Try again in a few seconds.",
    "payload_too_large": "That request is too large. Ratchet only accepts small JSON bodies.",
}

# Short user-facing text per code (pair evidence, opinion reasons, badges).
REASON_TEXT: Final[dict[str, str]] = {
    "tier_mismatch": "Replay refused: model tier differs from the original run.",
    "budget_exceeded": "Paused: spending cap reached.",
    "upstream_unavailable": "Model unavailable; judged as uncertain.",
    "judge_timeout": "Final judge timed out; held for review.",
    "schema_failure": "Judge answer unreadable; treated as uncertain.",
    "replay_infra": "Replays failed; held for review.",
    "sandbox_failed": "Sandbox error on this task.",
    "harness_failed": "Agent crashed during replay.",
    "harness_mismatch": "Cannot replay: different agent runtime.",
    "drift_unavailable": "Could not verify whether the world changed.",
    "merge_conflict": "Held: skill files conflict with main.",
    "stacked_on_held": "Held: an earlier held edit must be resolved first.",
    "no_affected_traces": "Passed: no blessed tasks use this skill yet.",
    "ratchet_blocked": "blocked action",
    "action_locked": "Someone else is acting on this verdict.",
    "not_public": "Not available on the demo.",
    "rate_limited": "Too many actions; wait a moment.",
    "worker_lost": "Replay worker stopped; held for review.",
    "simulate_disabled": "Simulate is off on this host.",
    "simulate_busy": "Simulate is busy; try again shortly.",
    "action_token_invalid": "Page out of date; reload.",
    "already_reverted": "Already reverted; main is unchanged.",
    "not_ready": "Still acting on this verdict.",
    "payload_too_large": "Request too large.",
}


def error_copy(code: str, **fields: object) -> str:
    template = ERROR_COPY.get(code)
    if template is None:
        return reason_text(code)
    return template.format(**fields) if fields else template.replace("{n} of {m} ", "")


def reason_text(code: str) -> str:
    return REASON_TEXT.get(code, "Something went wrong; Ratchet treated it as not sure.")


# Simulate refuses a second run from the same client while its first is in flight.
SIMULATE_BUSY_CLIENT: Final = (
    "You already have a scenario running. Wait for it to finish, then try another."
)
