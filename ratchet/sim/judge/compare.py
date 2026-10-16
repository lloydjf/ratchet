"""Golden-vs-replay comparison behind the simulated screen / check / adjudicate opinions.

Rules (design doc §4.2), first match wins:

1. identical after whitespace normalization → ``identical``;
2. a *significant* golden line — a ``Key: value`` line, or a line a success criterion
   names — has fewer copies in the replay (numbers masked, so a changed figure is not a
   missing line) → ``missing_line``;
3. a tool the golden called is never called by the replay → ``missing_tool`` (an addition:
   the judge prompts count a missing required action as a regression);
4. the texts are equal once every number is masked → ``numbers_only`` (possible drift);
5. token Jaccard ≥ 0.9 → ``similar``; otherwise ``different``.

Pure functions over the section text; trace content is data, never instructions.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from ratchet.sim.text import content_lines, jaccard, kv, mask_numbers, normalize_ws

__all__ = [
    "SIMILAR_JACCARD",
    "Kind",
    "Comparison",
    "compare",
    "tools_in",
    "step_ids_in",
    "numeric_changes",
]

SIMILAR_JACCARD = 0.9
Kind = Literal["identical", "missing_line", "missing_tool", "numbers_only", "similar", "different"]
_CALL = re.compile(r"\b([a-z]\d{3,})\s+(?:\|\s+tool\s+)?([a-z_]+\.[a-z_]+)\s+\(")
_STEP = re.compile(r"(?:^|;\s*|\n)\s*([a-z]\d{3,})\b")


@dataclass(frozen=True)
class Comparison:
    kind: Kind
    jaccard: float
    line: str | None = None  # the golden line the replay lost
    key: str | None = None  # its key, for Key: value lines
    tool: str | None = None
    changes: list[tuple[str, str]] = field(default_factory=list)  # (golden, replay) lines


def tools_in(summary: str) -> list[tuple[str, str]]:
    """(step id, tool) pairs from a call summary or step listing, Level 0 notes excluded."""
    return _CALL.findall(summary.split("\nLevel 0 (deterministic):", 1)[0])


def step_ids_in(summary: str) -> list[str]:
    return list(dict.fromkeys(_STEP.findall(summary.split("\nLevel 0 (deterministic):", 1)[0])))


def _named_by_criteria(line: str, criteria: list[str]) -> bool:
    return len(line) >= 8 and any(line in c for c in criteria)


def _missing_line(golden: list[str], replay: list[str], criteria: list[str]) -> str | None:
    have = Counter(mask_numbers(line) for line in replay)
    need: Counter[str] = Counter()
    for line in golden:
        if kv(line) is None and not _named_by_criteria(line, criteria):
            continue
        masked = mask_numbers(line)
        need[masked] += 1
        if need[masked] > have[masked]:
            # Report the copy the replay lost: the first one not present verbatim.
            same = [g for g in golden if mask_numbers(g) == masked]
            return next((g for g in same if g not in replay), line)
    return None


def numeric_changes(golden: list[str], replay: list[str]) -> list[tuple[str, str]]:
    """Line pairs (by position) that differ only in their numbers."""
    return [
        (g, r)
        for g, r in zip(golden, replay, strict=False)
        if g != r and mask_numbers(g) == mask_numbers(r)
    ]


def compare(
    golden_out: str,
    replay_out: str,
    *,
    criteria: list[str] | None = None,
    golden_tools: list[str] | None = None,
    replay_tools: list[str] | None = None,
) -> Comparison:
    golden, replay = content_lines(golden_out), content_lines(replay_out)
    score = jaccard(golden_out, replay_out)
    if normalize_ws(golden_out) == normalize_ws(replay_out):
        return Comparison("identical", 1.0)
    lost = _missing_line(golden, replay, criteria or [])
    if lost is not None:
        pair = kv(lost)
        return Comparison("missing_line", score, line=lost, key=pair[0] if pair else None)
    gone = [t for t in dict.fromkeys(golden_tools or []) if t not in set(replay_tools or [])]
    if gone:
        return Comparison("missing_tool", score, tool=gone[0])
    if mask_numbers(normalize_ws(golden_out)) == mask_numbers(normalize_ws(replay_out)):
        return Comparison("numbers_only", score, changes=numeric_changes(golden, replay))
    return Comparison("similar" if score >= SIMILAR_JACCARD else "different", score)
