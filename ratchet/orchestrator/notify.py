"""Verdict notifications (structured payload, one-line message, retries).

One JSON body serves both receivers: Slack-compatible incoming webhooks read ``text``;
generic receivers read the structured fields and dedupe on ``verdict_id``.
Delivery is best effort: 5 s timeout, 2 retries 2 s apart, never raises.
"""

from __future__ import annotations

import asyncio
import re

import httpx

from ratchet.config.settings import Settings, get_settings
from ratchet.log import get_logger
from ratchet.models import SkillEdit, Verdict

__all__ = ["MESSAGE_LIMIT", "format_message", "first_sentence", "build_payload", "notify"]

MESSAGE_LIMIT = 160
_TIMEOUT_S = 5.0
_RETRIES = 2
_ELLIPSIS = "…"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_log = get_logger("notify")


def first_sentence(text: str) -> str:
    text = " ".join(text.split())
    return _SENTENCE_END.split(text, maxsplit=1)[0] if text else ""


def format_message(decision: str, headline: str, url: str) -> str:
    """``Ratchet {decision}: {headline} — {url}`` in at most 160 characters.

    Only the headline is shortened (with an ellipsis); the link is never cut.
    """
    prefix, sep = f"Ratchet {decision}: ", " — "
    headline = " ".join(headline.split())
    room = MESSAGE_LIMIT - len(prefix) - len(sep) - len(url)
    if len(headline) > room:
        headline = headline[: max(room - 1, 0)].rstrip() + _ELLIPSIS if room > 1 else ""
    if not headline:
        return f"Ratchet {decision} — {url}"[:MESSAGE_LIMIT]
    return f"{prefix}{headline}{sep}{url}"


def build_payload(verdict: Verdict, edit: SkillEdit, public_url: str) -> dict[str, object]:
    """The structured payload plus ``text`` (the one-line message) and a few additive fields."""
    url = f"{public_url.rstrip('/')}/verdicts/{verdict.verdict_id}"
    summary = first_sentence(verdict.narrative)
    headline = verdict.headline or summary or edit.summary
    return {
        "verdict_id": verdict.verdict_id,
        "decision": verdict.decision,
        "action": verdict.action,
        "edit": {
            "edit_id": edit.edit_id,
            "affected_skills": list(edit.affected_skills),
            "author": edit.author,
        },
        "summary": summary,
        "headline": headline,
        "cost_usd": round(sum(verdict.cost.values()), 6),
        "url": url,
        "simulated_models": verdict.simulated_models,
        "text": format_message(verdict.decision, headline, url),
    }


def _retryable(response: httpx.Response) -> bool:
    return response.status_code == 429 or response.status_code >= 500


async def _post(client: httpx.AsyncClient, url: str, payload: dict[str, object]) -> str:
    """One attempt; ``"ok"``, ``"retry"`` or ``"fail"``."""
    try:
        response = await client.post(url, json=payload, timeout=_TIMEOUT_S)
    except httpx.HTTPError as exc:
        _log.warning("notify.attempt_failed", error_type=type(exc).__name__)
        return "retry"
    if response.is_success:
        return "ok"
    _log.warning("notify.attempt_failed", status=response.status_code)
    return "retry" if _retryable(response) else "fail"


async def notify(
    payload: dict[str, object],
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    backoff_s: float = 2.0,
) -> bool:
    """POST ``payload`` to ``ratchet_notify_webhook``; returns whether it was delivered."""
    settings = settings or get_settings()
    secret = settings.ratchet_notify_webhook
    webhook = secret.get_secret_value() if secret is not None else None
    verdict_id = str(payload.get("verdict_id") or "")
    if not webhook:
        _log.info("notify.skipped", verdict_id=verdict_id, reason="no_webhook")
        return False
    http = client or httpx.AsyncClient()
    try:
        for attempt in range(_RETRIES + 1):
            outcome = await _post(http, webhook, payload)
            if outcome == "ok":
                _log.info("notify.delivered", verdict_id=verdict_id, attempts=attempt + 1)
                return True
            if outcome == "fail" or attempt == _RETRIES:
                break
            await asyncio.sleep(backoff_s)
    except Exception as exc:  # noqa: BLE001 - notification must never fail the pipeline
        _log.error("notify.error", verdict_id=verdict_id, error_type=type(exc).__name__)
    finally:
        if client is None:
            await http.aclose()
    _log.warning("notify.failed", verdict_id=verdict_id)
    return False
