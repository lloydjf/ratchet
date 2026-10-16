"""Simulated ``screen`` / ``check`` / ``adjudicate`` opinions (design doc §4.2, ADR 0018).

Each handler parses its prompt's sections, runs :func:`ratchet.sim.judge.compare.compare`
and answers the prompt's schema (``ScreenOut`` / ``CheckOut`` / ``AdjudicateOut``):

==============  ===========  ==========  ===========  ===================================
comparison      verdict      screen      check        adjudicate
==============  ===========  ==========  ===========  ===================================
identical       equivalent   0.95        0.95         0.95
missing_line    regressed    0.93        0.90         0.92
missing_tool    regressed    0.90        0.88         0.90
numbers_only    uncertain    0.60        0.60         0.60 + ``possible_drift_fact``
similar         equivalent   0.86        0.86         0.86
different       uncertain    0.50        0.50         0.50
==============  ===========  ==========  ===========  ===================================

Cited step ids are taken only from the step listings in the prompt. Simulated opinions are
test infrastructure, never a Nemotron judgement.
"""

from __future__ import annotations

import re

from ratchet.sim.judge.compare import Comparison, compare, step_ids_in, tools_in
from ratchet.sim.judge.sections import parse_sections
from ratchet.sim.text import bullet_items, content_lines, kv

__all__ = ["CONFIDENCE", "VERDICTS", "screen", "check", "adjudicate", "criteria_met"]

VERDICTS = {
    "identical": "equivalent",
    "missing_line": "regressed",
    "missing_tool": "regressed",
    "numbers_only": "uncertain",
    "similar": "equivalent",
    "different": "uncertain",
}
CONFIDENCE: dict[str, dict[str, float]] = {
    "screen": {"missing_line": 0.93, "missing_tool": 0.90},
    "check": {"missing_line": 0.90, "missing_tool": 0.88},
    "adjudicate": {"missing_line": 0.92, "missing_tool": 0.90},
}
_BASE_CONFIDENCE = {"identical": 0.95, "numbers_only": 0.6, "similar": 0.86, "different": 0.5}
_KEY_CRITERION = re.compile(r"includes the (.+?) line\b", re.IGNORECASE)
_TOOL_CRITERION = re.compile(r"\b([a-z_]+\.[a-z_]+) is called\b")


def _reason(c: Comparison) -> list[str]:
    if c.kind == "identical":
        return ["The replay output matches the golden output."]
    if c.kind == "missing_line":
        return [f"Replay output no longer includes '{c.line}'"]
    if c.kind == "missing_tool":
        return [f"The replay never calls {c.tool}, which the golden run used."]
    if c.kind == "numbers_only":
        first = c.changes[0] if c.changes else None
        changed = f": '{first[0]}' became '{first[1]}'" if first else ""
        return [
            f"Only figures differ between the outputs{changed}.",
            "This may follow from a changed external fact.",
        ]
    if c.kind == "similar":
        return [f"The outputs differ only in wording (token overlap {c.jaccard:.2f})."]
    return ["The outputs differ and the evidence does not show which one is correct."]


def _criteria(sections: dict[str, str]) -> list[str]:
    return bullet_items(sections.get("criteria", ""))


def _key_count(text: str, key: str) -> int:
    return sum(1 for line in content_lines(text) if (pair := kv(line)) and pair[0] == key)


def criteria_met(
    criteria: list[str], c: Comparison, golden: str, replay: str, replay_tools: list[str]
) -> list[dict[str, object]]:
    """Per-criterion findings, unmet first; the lost line is always named."""
    found: list[dict[str, object]] = []
    for criterion in criteria:
        key, tool = _KEY_CRITERION.search(criterion), _TOOL_CRITERION.search(criterion)
        if key:
            met = _key_count(replay, key.group(1)) >= _key_count(golden, key.group(1))
        elif tool:
            met = tool.group(1) in replay_tools
        else:
            met = not (c.line and c.line in criterion) and VERDICTS[c.kind] != "regressed"
        found.append({"criterion": criterion, "met": met, "evidence_steps": []})
    if c.kind == "missing_line" and not any(not f["met"] for f in found):
        name = f"The output includes the {c.key} line" if c.key else f"Includes '{c.line}'"
        found.insert(0, {"criterion": name, "met": False, "evidence_steps": []})
    if c.kind == "missing_tool" and not any(not f["met"] for f in found):
        found.insert(0, {"criterion": f"{c.tool} is called", "met": False, "evidence_steps": []})
    return sorted(found, key=lambda f: bool(f["met"]))


Opinion = tuple[Comparison, dict[str, object], list[str]]


def _opinion(prompt: str, sections: dict[str, str], golden_key: str, replay_key: str) -> Opinion:
    golden_calls = tools_in(sections.get(golden_key, ""))
    replay_calls = tools_in(sections.get(replay_key, ""))
    c = compare(
        sections.get("golden_output", ""),
        sections.get("replay_output", ""),
        criteria=_criteria(sections),
        golden_tools=[t for _, t in golden_calls],
        replay_tools=[t for _, t in replay_calls],
    )
    confidence = CONFIDENCE[prompt].get(c.kind, _BASE_CONFIDENCE.get(c.kind, 0.5))
    evidence = step_ids_in(sections.get(replay_key, ""))[-1:]
    if c.kind == "missing_tool":
        evidence += [s for s, t in golden_calls if t == c.tool][:1]
    out: dict[str, object] = {
        "verdict": VERDICTS[c.kind],
        "confidence": confidence,
        "reasons": _reason(c),
        "evidence_steps": evidence[:4],
    }
    return c, out, [t for _, t in replay_calls]


def _findings(sections: dict[str, str], c: Comparison, tools: list[str]) -> list[dict[str, object]]:
    golden, replay = sections.get("golden_output", ""), sections.get("replay_output", "")
    return criteria_met(_criteria(sections), c, golden, replay, tools)


def screen(text: str) -> dict[str, object]:
    sections = parse_sections("screen", text)
    _, out, _ = _opinion("screen", sections, "golden_calls", "replay_calls")
    return out


def check(text: str) -> dict[str, object]:
    sections = parse_sections("check", text)
    c, out, tools = _opinion("check", sections, "golden_steps", "replay_steps")
    out["criteria_met"] = _findings(sections, c, tools)
    return out


def _causal(c: Comparison) -> str:
    if c.kind == "numbers_only" and c.changes:
        old, new = c.changes[0]
        return (
            f"Only figures differ: '{old}' became '{new}'. A changed external value explains "
            "this better than the skill diff."
        )
    if VERDICTS[c.kind] == "regressed":
        lost = c.line or c.tool or "a required element"
        return f"The replay lost {lost!r}, and the skill diff changes the step that produces it."
    if VERDICTS[c.kind] == "equivalent":
        return "The differences are cosmetic; every criterion is still met."
    return "The evidence does not show whether the skill diff caused the difference."


def adjudicate(text: str) -> dict[str, object]:
    sections = parse_sections("adjudicate", text)
    c, out, tools = _opinion("adjudicate", sections, "golden_steps", "replay_steps")
    out["criteria_met"] = _findings(sections, c, tools)
    out["causal_explanation"] = _causal(c)
    drift = None
    if c.kind == "numbers_only" and c.changes:
        rate_lines = [old for old, _ in c.changes if "rate" in old.lower()]
        drift = rate_lines[0] if rate_lines else c.changes[0][0]
    out["possible_drift_fact"] = drift
    # Causality: an empty or placeholder diff cannot explain a difference.
    diff = sections.get("skill_diff", "").strip()
    out["caused_by_diff"] = not (out["verdict"] == "regressed" and _diff_is_empty(diff))
    return out


def _diff_is_empty(diff: str) -> bool:
    return diff in ("", "(no diff available)") or not any(
        line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
        for line in diff.splitlines()
    )
