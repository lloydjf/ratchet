"""Bless-time enrichment of a golden trace (part of the bless pipeline).

``enrich`` adds, with Nano only:

1. world facts (``extract_world_facts.md``) from the final output and every tool result
   (the trace's ``ToolCall.result_preview``; previews hold 400 chars, the prompt allows 600);
   each fact keeps its bare ``value``; invented ``source_step`` ids are cleared, duplicate or
   empty ``fact_id`` values are renumbered;
2. side-effect classes for tool calls: rule table first, Nano only for unmatched tool names
   (cached per tool);
3. success criteria (``infer_success_criteria.md``) when the trace has none yet;
4. ``world_dependent = len(facts) > 0 or any tool in {web.search, web.fetch, time.now,
   weather.*, finance.*}``;
5. ``blessed_at`` (kept if already set).

A failed Nano call leaves that part empty rather than inventing content. ``kind`` is not
changed here; ``Store.mark_golden`` / the caller owns it.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase

from ratchet import clock
from ratchet.judge import side_effects
from ratchet.judge.prompts import get_spec
from ratchet.judge.schemas import CriteriaOut, WorldFactOut, WorldFactsOut
from ratchet.judge.structured import ChatClient, call_structured
from ratchet.judge.summaries import tool_results_text
from ratchet.log import get_logger
from ratchet.models import LLMCall, ToolCall, Trace, WorldFact
from ratchet.tools.catalog import get_tool

__all__ = [
    "is_format_criterion",
    "value_in",
    "WORLD_TOOLS",
    "uses_world_tools",
    "enrich",
    "extract_facts",
    "infer_criteria",
]

WORLD_TOOLS: tuple[str, ...] = ("web.search", "web.fetch", "time.now", "weather.*", "finance.*")
_log = get_logger("judge.bless")


def uses_world_tools(trace: Trace) -> bool:
    return any(
        isinstance(s, ToolCall) and any(fnmatchcase(s.tool, p) for p in WORLD_TOOLS)
        for s in trace.steps
    )


def _artifact_list(trace: Trace) -> str:
    paths = [
        f"{s.tool} {s.args_norm.get('path', '')}".strip()
        for s in trace.steps
        if isinstance(s, ToolCall) and s.side_effect != "read"
    ]
    return ", ".join(paths) or "(none)"


def _grounded(facts: list[WorldFactOut], trace: Trace) -> list[WorldFactOut]:
    """Keep only facts a drift check can honestly re-ground (found in live runs: Nano lists the
    user's own issues, receipts and arithmetic as "facts"). A fact survives only if it came
    from a world-dependent tool (catalog ``world_dependent``: finance.*, web.*, time.now)
    and its value appears in that tool's result."""
    world_steps = {
        s.step_id: s.result_preview
        for s in trace.steps
        if isinstance(s, ToolCall)
        and (spec := get_tool(s.tool)) is not None
        and spec.world_dependent
    }
    kept: list[WorldFactOut] = []
    seen: set[tuple[str, str]] = set()
    for fact in facts:
        preview = world_steps.get(fact.source_step)
        value = fact_value(fact.value, fact.statement)
        key = (fact.source_step, value)
        if preview is None or key in seen or _ISO_DATE.fullmatch(value):
            continue  # not from a world tool, a repeat, or just the observation date
        if value_in(value, preview):
            seen.add(key)
            kept.append(fact.model_copy(update={"value": value}))
    return kept


def fact_value(value: str, statement: str) -> str:
    """The bare value; when the model left it out, the last number in the statement
    ("The EUR/USD exchange rate on 2026-10-14 was 1.0800" gives "1.0800")."""
    if value.strip():
        return value.strip()
    numbers = _NUMBER.findall(statement.split(" was ")[-1]) or _NUMBER.findall(statement)
    return numbers[-1] if numbers else ""


_NUMBER = re.compile(r"-?\d+(?:[.,]\d+)?")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def value_in(value: str, text: str) -> bool:
    """Is ``value`` present in ``text``? Numbers compare numerically and ignore units and
    formatting ("1.0800" == "1.08"; "1.11969431 USD" matches "= 1.11969431 USD")."""
    value = value.strip()
    if not value:
        return False
    if value in text:
        return True
    numbers = _NUMBER.findall(value.replace(",", ""))
    if len(numbers) != 1:
        return False  # not a single quantity: only an exact textual match counts
    wanted = float(numbers[0])
    return any(abs(float(n.replace(",", ".")) - wanted) < 1e-9 for n in _NUMBER.findall(text))


async def extract_facts(trace: Trace, client: ChatClient) -> list[WorldFact]:
    spec = get_spec("extract_world_facts")
    result = await call_structured(
        client,
        prompt=spec.name,
        tier=spec.tier,
        inputs={
            "task_input": trace.task_input,
            "final_output": trace.final_output,
            "tool_results": tool_results_text(trace, 600),
        },
        schema=WorldFactsOut,
        max_tokens=spec.max_tokens,
    )
    if result.value is None:
        return []
    ids = {s.step_id for s in trace.steps}
    facts: list[WorldFact] = []
    seen: set[str] = set()
    for i, out in enumerate(_grounded(result.value.facts, trace), start=1):
        fact_id = out.fact_id.strip()
        if not fact_id or fact_id in seen:
            fact_id = f"f{i}"
        seen.add(fact_id)
        facts.append(
            WorldFact(
                fact_id=fact_id,
                statement=out.statement,
                source_step=out.source_step if out.source_step in ids else "",
                volatile=out.volatile,
                query_hint=out.query_hint or out.statement,
                value=out.value,
            )
        )
    return facts


async def infer_criteria(trace: Trace, client: ChatClient) -> list[str]:
    spec = get_spec("infer_success_criteria")
    result = await call_structured(
        client,
        prompt=spec.name,
        tier=spec.tier,
        inputs={
            "task_input": trace.task_input,
            "final_output": trace.final_output,
            "artifact_list": _artifact_list(trace),
        },
        schema=CriteriaOut,
        max_tokens=spec.max_tokens,
    )
    if result.value is None:
        return []
    return [c.strip() for c in result.value.criteria if c.strip() and not is_format_criterion(c)]


_FORMAT_WORDS = re.compile(
    r"\*\*|`|\b(bold|italic|markdown|formatt?ed|formatting|capitali[sz]ed|punctuation|"
    r"code span|backtick|line break|the phrase)\b",
    re.IGNORECASE,
)


def is_format_criterion(criterion: str) -> bool:
    """Criteria about typesetting rather than content (in live runs, 'includes the phrase
    "= **40.8**"' made a dropped bold marker a regression)."""
    return bool(_FORMAT_WORDS.search(criterion))


async def _classify_steps(
    trace: Trace, client: ChatClient, store: side_effects.SideEffectCache
) -> list[LLMCall | ToolCall]:
    steps: list[LLMCall | ToolCall] = []
    for step in trace.steps:
        if isinstance(step, ToolCall):
            cls, _ = await side_effects.classify(step.tool, step.args_norm, client, store)
            steps.append(step.model_copy(update={"side_effect": cls}))
        else:
            steps.append(step)
    return steps


async def enrich(trace: Trace, client: ChatClient, store: side_effects.SideEffectCache) -> Trace:
    steps = await _classify_steps(trace, client, store)
    classified = trace.model_copy(update={"steps": steps})
    facts = await extract_facts(classified, client)
    criteria = classified.success_criteria or await infer_criteria(classified, client)
    world_dependent = bool(facts) or uses_world_tools(classified)
    _log.info(
        "judge.bless",
        run_id=trace.run_id,
        n_facts=len(facts),
        n_criteria=len(criteria),
        world_dependent=world_dependent,
    )
    return Trace.model_validate(
        {
            **classified.model_dump(),
            "world_facts": [f.model_dump() for f in facts],
            "success_criteria": criteria,
            "world_dependent": world_dependent,
            "blessed_at": trace.blessed_at or clock.now(),
        }
    )
