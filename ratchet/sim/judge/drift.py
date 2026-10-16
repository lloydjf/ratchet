"""Simulated drift prompts: ``compare_fact`` and ``attribute_drift`` (ADR 0018).

- ``compare_fact``: decimal numbers in the search results (dates removed) within a factor of
  two of the observed value are candidates; the most frequent one is the current value.
  Different from the observed value → ``changed`` (0.9); equal → unchanged (0.85); no
  candidate, or results that never name the fact's currency codes → unchanged (0.3).
- ``attribute_drift``: attributable when the replay output contains a fact's new value
  (0.9 when the outputs differ only in figures, else 0.8).

Test infrastructure, never a Nemotron judgement.
"""

from __future__ import annotations

import re
from collections import Counter

from ratchet.sim.judge.sections import parse_sections
from ratchet.sim.text import decimals, mask_numbers, normalize_ws

__all__ = ["compare_fact", "attribute_drift", "same_number"]

_CODES = re.compile(r"\b[A-Z]{3}\b")
_CHANGE = re.compile(r"old:\s*([^|]+?)\s*\|\s*new:\s*([^|]+?)\s*(?:\||$)", re.MULTILINE)
_ANY_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def same_number(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return False


def _old(value: str) -> str | None:
    found = decimals(value) or _ANY_NUMBER.findall(value)
    return found[0] if found else None


def compare_fact(text: str) -> dict[str, object]:
    s = parse_sections("compare_fact", text)
    results, old = s.get("search_results", ""), _old(s.get("old_value", ""))
    codes = set(_CODES.findall(s.get("statement", ""))) - {"USA", "THE"}
    unrelated = bool(codes) and not any(code in results for code in codes)
    if old is None or unrelated:
        return {"changed": False, "new_value": None, "confidence": 0.3}
    base = float(old)
    candidates = [n for n in decimals(results) if base > 0 and 0.5 <= float(n) / base <= 2.0]
    if not candidates:
        return {"changed": False, "new_value": None, "confidence": 0.3}
    counts = Counter(float(n) for n in candidates)
    best = max(counts.values())
    winner = next(n for n in candidates if counts[float(n)] == best)
    if same_number(winner, old):
        return {"changed": False, "new_value": None, "confidence": 0.85}
    return {"changed": True, "new_value": winner, "confidence": 0.9}


def attribute_drift(text: str) -> dict[str, object]:
    s = parse_sections("attribute_drift", text)
    golden, replay = s.get("golden_output", ""), s.get("replay_output", "")
    in_replay = decimals(replay) + _ANY_NUMBER.findall(replay)
    figures_only = mask_numbers(normalize_ws(golden)) == mask_numbers(normalize_ws(replay))
    for _, new in _CHANGE.findall(s.get("changed_facts", "")):
        value = _old(new)
        if value is not None and any(same_number(value, n) for n in in_replay):
            return {
                "attributable": True,
                "confidence": 0.9 if figures_only else 0.8,
                "reason": f"The replay output uses the new value {value}.",
            }
    return {
        "attributable": False,
        "confidence": 0.7,
        "reason": "The replay output does not contain any of the new values.",
    }
