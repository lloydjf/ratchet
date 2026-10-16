"""Verdict narrative and weekly digest, with the copy constraints.

One Ultra call per verdict, thinking off, ``max_tokens=400``. The output is checked against
the copy rules: at most 180 words (250 for the digest), no headings or bullet points, the first
sentence starts with the decision word (Blocked / Held / Passed) and names the skill (or is
exactly the deterministic headline), and the narrative ends with one of the four closing
sentences. Any failure — a model error, a timeout, or a text that breaks a rule — falls back to
a deterministic narrative built from :mod:`ratchet.copy` templates (``fallback_used=True``).
The prompt is given the deterministic headline and closing sentence to anchor it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from ratchet import copy
from ratchet.errors import RatchetError
from ratchet.judge.prompts import get_spec
from ratchet.judge.structured import ChatClient, build_prompt
from ratchet.log import get_logger
from ratchet.models import HARD_SIDE_EFFECTS, Action, Decision, PairVerdict, Tier, Verdict

__all__ = [
    "NarrativeInput",
    "NarrativeResult",
    "DigestInput",
    "headline_for",
    "closing_for",
    "first_sentence",
    "validate_narrative",
    "fallback_narrative",
    "write_narrative",
    "write_digest",
]

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_LIST_LINE = re.compile(r"^\s*([-*•]|\d+[.)]|#)", re.MULTILINE)
_SENTENCE = re.compile(r"^(.+?[.!?])(?=\s|$)", re.DOTALL)
_log = get_logger("judge.narrative")


@dataclass(frozen=True)
class NarrativeInput:
    decision: Decision
    action: Action
    skills: list[str]
    pairs: list[PairVerdict]
    cost_by_tier: dict[Tier, float]
    reasons: list[str] = field(default_factory=list)
    diff_summary: str = ""
    n_total: int | None = None  # pairs attempted (defaults to len(pairs))


@dataclass(frozen=True)
class NarrativeResult:
    text: str
    headline: str
    usd: float
    fallback_used: bool


@dataclass(frozen=True)
class DigestInput:
    period_start: str
    period_end: str
    verdicts: list[Verdict]
    cost_by_tier: dict[Tier, float] = field(default_factory=dict)


def _skill(inp: NarrativeInput) -> str:
    return ", ".join(inp.skills) if inp.skills else "the edited skill"


def _criterion(pair: PairVerdict) -> str:
    for opinion in reversed(pair.opinions):
        failed = next((c.criterion for c in opinion.criteria_met if not c.met), None)
        if failed:
            return failed
    return pair.reason.removesuffix(" is no longer met.").rstrip(".") or "a success criterion"


# Hold reasons about the edit as a whole, not about any replay (no "n of m" count).
_EDIT_LEVEL_REASONS = frozenset({"merge_conflict", "stacked_on_held"})


def _failure(pair: PairVerdict) -> str:
    """The headline's "now <failure>": an unmet criterion if a judge named one, else a
    judge's short failure summary (Ultra first), never a judge's free-text explanation."""
    for opinion in reversed(pair.opinions):
        unmet = next((c.criterion for c in opinion.criteria_met if not c.met), None)
        if unmet and len(unmet.split()) <= 16:
            return copy.failure_phrase(unmet)
    for opinion in reversed(pair.opinions):
        if opinion.failure_summary:
            return opinion.failure_summary
    return "no longer meets its success criteria"


def _dominant_hard_tool(pairs: list[PairVerdict]) -> str | None:
    """The new hard-class tool that appears in the most failing replays (ties: first seen),
    so the headline names the action that actually defines the regression."""
    counts: dict[str, int] = {}
    for pair in pairs:
        tools = {c.tool for c in pair.level0.new_calls if c.side_effect in HARD_SIDE_EFFECTS}
        for tool in sorted(tools):
            counts[tool] = counts.get(tool, 0) + 1
    return max(counts, key=lambda t: counts[t]) if counts else None


def headline_for(inp: NarrativeInput) -> str:
    """The deterministic headline for a decision."""
    m = inp.n_total if inp.n_total is not None else len(inp.pairs)
    skill = _skill(inp)
    if inp.decision == "PASS":
        if "no_affected_traces" in inp.reasons or m == 0:
            return copy.headline_no_traces(skill)
        n_drift = sum(p.outcome == "drift" for p in inp.pairs)
        return copy.headline_pass_drift(m, n_drift) if n_drift else copy.headline_pass(skill, m)
    if inp.decision == "BLOCK":
        level0 = [p for p in inp.pairs if p.decided_by == "level0" and p.level0.hard_fail]
        if level0:
            tool = _dominant_hard_tool(level0)
            action = copy.action_phrase(tool) if tool else "take a forbidden action"
            return copy.headline_block_level0(skill, action, len(level0), m)
        bad = [p for p in inp.pairs if p.outcome == "regression"]
        failure = _failure(bad[0]) if bad else "fails its checks"
        return copy.headline_block_cascade(skill, failure, len(bad), m)
    code = inp.reasons[0] if inp.reasons else "uncertain"
    phrase = copy.WARN_REASON_PHRASES.get(code, copy.WARN_REASON_PHRASES["uncertain"])
    if code in _EDIT_LEVEL_REASONS:
        return copy.headline_held_edit(phrase)
    flagged = sum(p.outcome in ("regression", "uncertain", "failed") for p in inp.pairs)
    return copy.headline_warn(phrase, max(flagged, 1) if m else 0, m)


def closing_for(inp: NarrativeInput) -> str:
    if inp.decision == "BLOCK":
        return copy.CLOSING_NOTHING
    if inp.decision == "WARN":
        if "single_judge_regression" in inp.reasons:
            return copy.CLOSING_ROLL_BACK
        return copy.CLOSING_APPROVE
    if any(p.outcome == "drift" for p in inp.pairs):
        return copy.CLOSING_REBLESS
    return copy.CLOSING_NOTHING


def first_sentence(text: str) -> str:
    match = _SENTENCE.match(text.strip())
    return match.group(1).strip() if match else text.strip()


def _words(text: str) -> int:
    return len(text.split())


def validate_narrative(text: str, inp: NarrativeInput, *, max_words: int | None = None) -> bool:
    limit = max_words or copy.NARRATIVE_MAX_WORDS
    clean = text.strip()
    if not clean or _words(clean) > limit or _LIST_LINE.search(clean) or "!" in clean:
        return False
    head = first_sentence(clean)
    if not head.startswith(copy.DECISION_WORDS[inp.decision]):
        return False
    named = any(s in head for s in inp.skills) or head == first_sentence(headline_for(inp))
    if not named:
        return False
    return any(clean.endswith(c) for c in copy.CLOSING_SENTENCES)


def _cost_text(cost: dict[Tier, float]) -> str:
    parts = [copy.cost_with_tier(usd, tier) for tier, usd in cost.items() if usd > 0]
    return ", ".join(parts) or copy.cost_with_tier(0.0, "nano")


def _label(pair: PairVerdict) -> str:
    return pair.task_label or (pair.task_input[:60] + ("…" if len(pair.task_input) > 60 else ""))


def _evidence_reason(pair: PairVerdict) -> str:
    """The pair's reason, phrased for the narrator. A Level 0 marker ("Checked without a
    model: ...") is UI copy that Ultra misread as describing the agent (live run), so Level 0
    findings are spelled out instead."""
    if pair.decided_by != "level0" or not pair.level0.hard_fail:
        return pair.reason
    hard = [c for c in pair.level0.new_calls if c.side_effect in HARD_SIDE_EFFECTS]
    tools = ", ".join(sorted({c.tool for c in hard})) or "a forbidden action"
    return (
        f"Ratchet's deterministic side-effect check found {len(hard)} new call(s) to {tools} "
        "that the original run never made; the sandbox refused them."
    )


def _evidence_lines(inp: NarrativeInput) -> list[str]:
    lines = []
    for pair in inp.pairs:
        if pair.outcome == "pass":
            continue
        steps = [s for o in pair.opinions for s in o.evidence_steps][:1]
        steps += [c.step_id for c in pair.level0.new_calls][:1] if not steps else []
        cite = f" (step {steps[0]})" if steps else ""
        outcome = copy.OUTCOME_LABELS[pair.outcome]
        line = f'"{_label(pair)}": {outcome}. {_evidence_reason(pair)}{cite}'
        moved = next((d for d in pair.drift if d.changed and d.new_value), None)
        if moved is not None:
            # Spell out the direction, or the narrator may state the old value as the new one.
            line += f" Tavily found the value moved from {moved.old_value} to {moved.new_value}."
        lines.append(line)
    return lines


def fallback_narrative(inp: NarrativeInput) -> str:
    m = inp.n_total if inp.n_total is not None else len(inp.pairs)
    parts = [headline_for(inp)]
    if m:
        parts.append(
            f"Ratchet replayed {m} past task{'s' if m != 1 else ''} against the edit to "
            f"{_skill(inp)} and compared each with its blessed result."
        )
    for line in _evidence_lines(inp)[:3]:
        candidate = " ".join([*parts, f"On {line}"])
        if _words(candidate) > copy.NARRATIVE_MAX_WORDS - 30:
            break
        parts.append(f"On {line}")
    parts.append(f"Judging cost {_cost_text(inp.cost_by_tier)}.")
    parts.append(closing_for(inp))
    return " ".join(p.strip() for p in parts)


def _inputs(inp: NarrativeInput) -> dict[str, str]:
    counts = Counter(p.outcome for p in inp.pairs)
    m = inp.n_total if inp.n_total is not None else len(inp.pairs)
    return {
        "decision": inp.decision,
        "action": inp.action,
        "skills": _skill(inp),
        "diff_summary": inp.diff_summary or "(no summary)",
        "n_pass": str(counts["pass"]),
        "n_regression": str(counts["regression"]),
        "n_drift": str(counts["drift"]),
        "n_uncertain": str(counts["uncertain"] + counts["failed"]),
        "n_improved": str(counts["improved"]),
        "n_total": str(m),
        "evidence_lines": "\n".join(f"- {line}" for line in _evidence_lines(inp)) or "(none)",
        "cost_by_tier": _cost_text(inp.cost_by_tier),
        "headline": headline_for(inp),
        "closing": closing_for(inp),
    }


async def _ultra_text(client: ChatClient, prompt: str, inputs: dict[str, str]) -> tuple[str, float]:
    spec = get_spec(prompt)
    rendered = build_prompt(prompt, inputs)
    chat = await client.chat(
        tier=spec.tier,
        purpose=spec.purpose,
        messages=[{"role": "user", "content": rendered.text}],
        max_tokens=spec.max_tokens,
        thinking=False,
        prompt_name=rendered.header,
    )
    text = _THINK.sub("", chat.text)
    text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    return text.strip(), chat.usd


async def write_narrative(inp: NarrativeInput, client: ChatClient) -> NarrativeResult:
    usd = 0.0
    try:
        text, usd = await _ultra_text(client, "narrative", _inputs(inp))
    except RatchetError as exc:
        _log.warning("narrative.failed", code=exc.code)
        text = ""
    if validate_narrative(text, inp):
        text = " ".join(text.split())
        return NarrativeResult(text, first_sentence(text), usd, False)
    _log.info("narrative.fallback", decision=inp.decision, had_text=bool(text))
    text = fallback_narrative(inp)
    return NarrativeResult(text, headline_for(inp), usd, True)


# --- digest -----------------------------------------------------------------------------------
def _digest_counts(inp: DigestInput) -> dict[str, int]:
    decisions = Counter(v.decision for v in inp.verdicts)
    return {
        "n_edits": len(inp.verdicts),
        "n_blocked": decisions["BLOCK"],
        "n_held": decisions["WARN"],
        "n_merged": sum(v.action == "merged" for v in inp.verdicts),
    }


def _digest_headline(inp: DigestInput) -> str:
    c = _digest_counts(inp)
    merged = sorted({s for v in inp.verdicts if v.action == "merged" for s in v.affected_skills})
    better = f" The agent got better at {', '.join(merged[:3])}." if merged else ""
    edits = "edit" if c["n_edits"] == 1 else "edits"
    return f"{c['n_edits']} skill {edits} checked this week; {c['n_blocked']} blocked.{better}"


def _digest_fallback(inp: DigestInput) -> str:
    c = _digest_counts(inp)
    parts = [_digest_headline(inp), f"{c['n_held']} held for review, {c['n_merged']} merged."]
    worst = next((v for v in inp.verdicts if v.decision == "BLOCK"), None) or next(
        (v for v in inp.verdicts if v.decision == "WARN"), None
    )
    if worst is not None and worst.headline:
        parts.append(f"Most important: {worst.headline}")
    if inp.cost_by_tier:
        parts.append(f"Judging cost {_cost_text(inp.cost_by_tier)}.")
    return " ".join(parts)


def _digest_valid(text: str) -> bool:
    clean = text.strip()
    if not clean or _words(clean) > copy.DIGEST_MAX_WORDS or _LIST_LINE.search(clean):
        return False
    return "!" not in clean and bool(re.search(r"\d", first_sentence(clean)))


async def write_digest(inp: DigestInput, client: ChatClient) -> NarrativeResult:
    lines = [
        f"{v.decision}, {', '.join(v.affected_skills) or '-'}, {v.headline}" for v in inp.verdicts
    ]
    inputs = {
        "period_start": inp.period_start,
        "period_end": inp.period_end,
        **{k: str(v) for k, v in _digest_counts(inp).items()},
        "verdict_lines": "\n".join(lines) or "(no verdicts)",
        "cost_by_tier": _cost_text(inp.cost_by_tier),
        "headline": _digest_headline(inp),
    }
    usd = 0.0
    try:
        text, usd = await _ultra_text(client, "digest", inputs)
    except RatchetError as exc:
        _log.warning("digest.failed", code=exc.code)
        text = ""
    if _digest_valid(text):
        text = " ".join(text.split())
        return NarrativeResult(text, first_sentence(text), usd, False)
    text = _digest_fallback(inp)
    return NarrativeResult(text, first_sentence(text), usd, True)
