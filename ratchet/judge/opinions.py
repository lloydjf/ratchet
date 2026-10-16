"""Judge opinions from structured results (split from :mod:`ratchet.judge.cascade`).

``valid_opinion`` turns a :class:`~ratchet.judge.structured.StructuredResult` into a
:class:`~ratchet.models.JudgeOpinion`: a failed call is an ``uncertain`` opinion carrying the
failure code (never a pass), cited step ids not in either trace are dropped and noted, and
reasons are cleaned of thinking-aloud items. ``cascade`` re-exports these names.
"""

from __future__ import annotations

import re
from typing import TypeVar

from ratchet.judge.schemas import AdjudicateOut, CheckOut, ScreenOut
from ratchet.judge.structured import StructuredResult
from ratchet.models import JudgeOpinion, Tier

__all__ = ["clean_reasons", "short_failure", "valid_opinion"]

S = TypeVar("S", bound=ScreenOut)
_MAX_EXPLANATION_WORDS = 80

_DELIBERATION = re.compile(
    r"^\s*(wait|hmm|actually|let me|re-?evaluating|on second thought)\b", re.I
)
_MAX_REASON_CHARS = 240


def clean_reasons(reasons: list[str]) -> list[str]:
    """Conclusions only: drop thinking-aloud items ("Wait, re-evaluating: ...") that a
    reasoning model sometimes writes into the answer, and cap each reason's length."""
    out: list[str] = []
    for reason in reasons:
        text = " ".join(reason.split())
        if not text or _DELIBERATION.match(text):
            continue
        if len(text) > _MAX_REASON_CHARS:
            cut = text[:_MAX_REASON_CHARS]
            end = max(cut.rfind(". "), cut.rfind("; "))
            text = cut[: end + 1] if end > 60 else cut.rstrip() + "…"
        out.append(text)
    if out or not reasons:
        return out
    return [" ".join(reasons[0].split())[:_MAX_REASON_CHARS]]  # never leave an opinion bare


def short_failure(text: str) -> str | None:
    """A failure summary is used in the headline only if it is a short plain phrase."""
    phrase = " ".join(text.split()).strip().rstrip(".")
    if not phrase or len(phrase.split()) > 10 or any(ch in phrase for ch in '\n:;"'):
        return None
    return phrase[0].lower() + phrase[1:]


def valid_opinion(tier: Tier, result: StructuredResult[S], ids: set[str]) -> JudgeOpinion:
    """A JudgeOpinion from a structured result; invented step ids are dropped and noted."""
    common = {
        "tier": tier,
        "usd": result.usd,
        "prompt_hash": result.prompt_hash,
        "truncated": result.truncated,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }
    value = result.value
    if value is None:
        code = result.failure_code or "schema_failure"
        return JudgeOpinion(
            verdict="uncertain", confidence=0.0, reasons=[code], evidence_steps=[], **common
        )
    cited = list(dict.fromkeys(value.evidence_steps))
    kept = [s for s in cited if s in ids]
    dropped = [s for s in cited if s not in ids]
    reasons = clean_reasons(value.reasons)
    criteria = []
    if isinstance(value, CheckOut):
        for crit in value.criteria_met:
            dropped += [s for s in crit.evidence_steps if s not in ids and s not in dropped]
            criteria.append(
                {
                    "criterion": crit.criterion,
                    "met": crit.met,
                    "evidence_steps": [s for s in crit.evidence_steps if s in ids],
                }
            )
    if dropped:
        reasons.append(f"Dropped cited step ids not found in the traces: {', '.join(dropped)}.")
    extra: dict[str, object] = {}
    if isinstance(value, CheckOut) and value.verdict == "regressed":
        extra["failure_summary"] = short_failure(value.failure_summary)
    if isinstance(value, AdjudicateOut):
        words = value.causal_explanation.split()
        extra["causal_explanation"] = " ".join(words[:_MAX_EXPLANATION_WORDS]) or None
        extra["possible_drift_fact"] = value.possible_drift_fact
        extra["caused_by_diff"] = value.caused_by_diff
    return JudgeOpinion.model_validate(
        {
            **common,
            "verdict": value.verdict,
            "confidence": value.confidence,
            "reasons": reasons,
            "evidence_steps": kept,
            "criteria_met": criteria,
            **extra,
        }
    )
