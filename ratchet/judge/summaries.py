"""Trace summaries rendered into judge prompts (the inputs each cascade level sees).

- screen (Nano): ``tool (side-effect class)`` per call — no args, no results.
- check (Super): one line per step — step id, tool or model, normalized args, result hash,
  first 400 chars of the result (or of the assistant text for a model step).
- adjudicate (Ultra): the same step lines plus full final outputs (traces keep only a
  400-char result preview, so "full outputs" means untruncated final outputs).

Unexpected side effects (cassette misses) are listed after the replay's steps and marked, so
a judge sees calls the mock server refused.
"""

from __future__ import annotations

from ratchet.models import JudgeOpinion, LLMCall, ToolCall, Trace
from ratchet.util import canonical_json, preview

__all__ = [
    "PREVIEW_CHARS",
    "call_summary",
    "step_lines",
    "criteria_text",
    "opinion_text",
    "step_ids",
    "tool_results_text",
]

PREVIEW_CHARS = 400
NONE_TEXT = "(none)"


def _unexpected_only(trace: Trace, unexpected: list[ToolCall]) -> list[ToolCall]:
    ids = {s.step_id for s in trace.steps}
    return [c for c in unexpected if c.step_id not in ids]


def call_summary(trace: Trace, unexpected: list[ToolCall] | None = None) -> str:
    calls = [s for s in trace.steps if isinstance(s, ToolCall)]
    calls += _unexpected_only(trace, unexpected or [])
    if not calls:
        return NONE_TEXT
    parts = []
    for call in calls:
        flag = ", blocked" if call.blocked else ""
        parts.append(f"{call.step_id} {call.tool} ({call.side_effect}{flag})")
    return "; ".join(parts)


def _tool_line(call: ToolCall, limit: int, marker: str = "") -> str:
    result = call.result_hash[:12] if call.result_hash else "-"
    blocked = " [blocked: ratchet_blocked]" if call.blocked else ""
    return (
        f"{call.step_id} | tool {call.tool} ({call.side_effect}){marker}{blocked} | "
        f"args {preview(canonical_json(call.args_norm), limit)} | result {result} | "
        f"{preview(call.result_preview, limit) or '-'}"
    )


def _llm_line(call: LLMCall, limit: int) -> str:
    asked = (
        f" requested {', '.join(call.tool_calls_requested)}" if call.tool_calls_requested else ""
    )
    text = preview(call.content_preview, limit) or "-"
    return f"{call.step_id} | model {call.tier}{asked} | {text}"


def step_lines(
    trace: Trace, unexpected: list[ToolCall] | None = None, limit: int = PREVIEW_CHARS
) -> str:
    lines = [
        _tool_line(s, limit) if isinstance(s, ToolCall) else _llm_line(s, limit)
        for s in trace.steps
    ]
    lines += [
        _tool_line(c, limit, " [unexpected]") for c in _unexpected_only(trace, unexpected or [])
    ]
    return "\n".join(lines) if lines else NONE_TEXT


def criteria_text(criteria: list[str]) -> str:
    if not criteria:
        return "(none recorded; judge against the golden final output)"
    return "\n".join(f"- {c}" for c in criteria)


def opinion_text(opinion: JudgeOpinion | None) -> str:
    if opinion is None:
        return "not consulted"
    reasons = "; ".join(opinion.reasons[:3]) or "no reasons given"
    return f"{opinion.verdict} ({opinion.confidence:.2f}): {reasons}"


def step_ids(*traces: Trace, extra: list[ToolCall] | None = None) -> set[str]:
    ids = {s.step_id for t in traces for s in t.steps}
    return ids | {c.step_id for c in extra or []}


def tool_results_text(trace: Trace, limit: int = 600) -> str:
    """Tool name and the first ``limit`` chars of each result, with step ids."""
    lines = [
        f"{s.step_id} {s.tool}: {preview(s.result_preview, limit) or '(empty)'}"
        for s in trace.steps
        if isinstance(s, ToolCall)
    ]
    return "\n".join(lines) if lines else NONE_TEXT
