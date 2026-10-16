"""Simulated plain-text prompts: ``narrative`` and ``digest``.

Both are filled from the prompt fields: the given headline first (it already follows the
headline template), then what was checked, the strongest evidence, the cost with tiers and —
for the narrative — the exact closing sentence the prompt asks for. One paragraph, no
lists, no exclamation marks, inside the word limits that ``ratchet.judge.narrative``
validates. Test infrastructure, never Nemotron prose.
"""

from __future__ import annotations

from ratchet.sim.judge.sections import parse_sections
from ratchet.sim.text import bullet_items

__all__ = ["narrative", "digest", "MAX_WORDS", "DIGEST_MAX_WORDS"]

MAX_WORDS = 170  # the validator allows 180
DIGEST_MAX_WORDS = 240  # the validator allows 250


def _clean(text: str) -> str:
    return " ".join(text.replace("!", ".").split())


def _sentence(text: str) -> str:
    text = _clean(text)
    return text if text.endswith((".", "?")) else f"{text}."


def _fits(parts: list[str], extra: str, limit: int) -> bool:
    return len(" ".join([*parts, extra]).split()) <= limit


def narrative(text: str) -> str:
    s = parse_sections("narrative", text)
    skills = s.get("skills", "the edited skill")
    total = s.get("n_total", "0")
    plural = "" if total == "1" else "s"
    parts = [_sentence(s.get("headline", "") or f"Held: {skills} needs review")]
    parts.append(
        f"Ratchet replayed {total} past task{plural} against the edit to {skills} and compared "
        "each with its blessed result."
    )
    parts.append(
        f"Outcomes: {s.get('n_pass', '0')} same, {s.get('n_regression', '0')} worse, "
        f"{s.get('n_drift', '0')} changed with the world, {s.get('n_uncertain', '0')} not sure."
    )
    closing = _clean(s.get("closing", ""))
    cost = _sentence(f"Judging cost {s.get('cost_by_tier', 'nothing')}")
    for line in bullet_items(s.get("evidence_lines", ""))[:2]:
        sentence = _sentence(f"On {line}")
        if not _fits([*parts, cost, closing], sentence, MAX_WORDS):
            break
        parts.append(sentence)
    parts.append(cost)
    if closing:
        parts.append(closing)
    return " ".join(parts)


def digest(text: str) -> str:
    s = parse_sections("digest", text)
    parts = [_sentence(s.get("headline", "") or f"{s.get('n_edits', '0')} skill edits checked")]
    parts.append(
        f"{s.get('n_held', '0')} held for review and {s.get('n_merged', '0')} merged between "
        f"{s.get('period_start', '')} and {s.get('period_end', '')}."
    )
    lines = [line.strip() for line in s.get("verdict_lines", "").splitlines() if line.strip()]
    worst = next((x for x in lines if x.startswith("BLOCK")), None) or next(
        (x for x in lines if x.startswith("WARN")), None
    )
    if worst is not None:
        headline = worst.split(", ", 2)[-1]
        sentence = _sentence(f"Most important: {headline}")
        if _fits(parts, sentence, DIGEST_MAX_WORDS):
            parts.append(sentence)
    cost = _sentence(f"Judging cost {s.get('cost_by_tier', 'nothing')}")
    if _fits(parts, cost, DIGEST_MAX_WORDS):
        parts.append(cost)
    return " ".join(parts)
