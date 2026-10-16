"""Per-fact drift checks (split from :mod:`ratchet.judge.drift`, steps 1-2).

Builds the Tavily search for one ``WorldFact`` (domain and date filtering), asks Nano (then
Super when Nano is unsure) whether the value changed, and keeps a comparison only when the
search evidence grounds it. Also reads facts straight off the traces (``observed_facts``,
``replay_value``). A Tavily failure is a ``DriftCheck(status="unavailable")``; unknown never
becomes ``changed``. :mod:`ratchet.judge.drift` re-exports the public names.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from typing import Literal
from urllib.parse import urlparse

from ratchet import clock, copy
from ratchet.errors import RatchetError
from ratchet.judge.bless import value_in
from ratchet.judge.prompts import get_spec
from ratchet.judge.schemas import CompareFactOut
from ratchet.judge.structured import ChatClient, call_structured
from ratchet.judge.tavily import SearchParams, TavilyResults, TavilySource
from ratchet.log import get_logger
from ratchet.models import DriftCheck, Tier, ToolCall, Trace, WorldFact
from ratchet.tools.catalog import get_tool
from ratchet.util import preview

__all__ = [
    "CHANGED_CONFIDENCE",
    "fact_domain",
    "current_query",
    "search_params",
    "observed_facts",
    "replay_value",
    "check_fact",
]

CHANGED_CONFIDENCE = 0.75
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_log = get_logger("judge.drift")


def _source_call(fact: WorldFact, golden: Trace) -> ToolCall | None:
    return next(
        (s for s in golden.steps if isinstance(s, ToolCall) and s.step_id == fact.source_step),
        None,
    )


def fact_domain(fact: WorldFact, golden: Trace) -> str | None:
    """The source domain of a fact, when the trace shows where it came from."""
    call = _source_call(fact, golden)
    texts: list[str] = []
    if call is not None:
        texts += [str(v) for k, v in call.args_norm.items() if k in ("url", "urls", "source")]
        texts.append(call.result_preview)
    texts.append(fact.statement)
    for text in texts:
        match = _URL.search(text)
        if match:
            host = urlparse(match.group(0)).hostname or ""
            host = host.removeprefix("www.")
            if "." in host:
                return host
    return None


def _observed_on(golden: Trace) -> date:
    return golden.env.frozen_time.date()


_DATE_IN_QUERY = re.compile(r"\s*\b(?:on\s+|as of\s+)?\d{4}-\d{2}-\d{2}\b")


def current_query(hint: str) -> str:
    """Drift asks what the value is *now*, so the observation date in a bless-time query
    hint ("EUR USD exchange rate 2026-10-14") is removed; the date filter is applied
    separately through ``time_range``/``start_date``."""
    stripped = _DATE_IN_QUERY.sub(" ", hint)
    cleaned = " ".join(stripped.split())
    if not cleaned:
        return hint
    if stripped != hint and not re.search(r"\b(today|now|current|latest)\b", cleaned, re.I):
        cleaned += " today"  # in live runs, "... today" gets a stated current value back
    return cleaned


def search_params(fact: WorldFact, golden: Trace) -> SearchParams:
    domain = fact_domain(fact, golden)
    observed = _observed_on(golden)
    # In live runs, topic="finance" returns quote pages whose content carries no usable value,
    # while "general" returns converter pages that state it, so every fact uses "general".
    topic: Literal["general", "finance"] = "general"
    old_claim = (clock.now().date() - observed).days > 7
    return SearchParams(
        search_depth="advanced",
        time_range=None if old_claim else "week",
        start_date=observed.isoformat() if old_claim else None,
        include_domains=[domain] if domain else [],
        topic=topic,
    )


def _results_text(results: TavilyResults) -> str:
    lines = [f"Tavily answer: {preview(results.answer, 500)}"] if results.answer else []
    lines += [
        f"- {h.title} | {h.url} | {h.published_date or 'undated'} | {preview(h.content, 700)}"
        for h in results.hits[:5]
    ]
    return "\n".join(lines) if lines else "(no results)"


async def _search(tavily: TavilySource, query: str, params: SearchParams) -> TavilyResults:
    results = await tavily.search(query, params)
    if not results.hits and params.include_domains:
        results = await tavily.search(query, replace(params, include_domains=[]))
    return results


async def check_fact(
    fact: WorldFact, golden: Trace, replay: Trace, client: ChatClient, tavily: TavilySource
) -> tuple[DriftCheck, float]:
    query = current_query(fact.query_hint or fact.statement)
    old_value = fact.value or fact.statement
    candidate = replay_value(fact, golden, replay)
    base: dict[str, object] = {
        "fact_id": fact.fact_id,
        "query": query,
        "old_value": old_value,
        "replay_value": candidate,
    }
    try:
        results = await _search(tavily, query, search_params(fact, golden))
    except RatchetError as exc:
        _log.warning("drift.unavailable", fact_id=fact.fact_id, code=exc.code)
        return DriftCheck(
            **base,
            tavily_results_ref="",
            changed=False,
            new_value=None,
            confidence=0.0,
            statement=fact.statement,
            status="unavailable",
            ruling=copy.reason_text("drift_unavailable"),
        ), 0.0
    evidence = _results_text(results)
    inputs = {
        "statement": fact.statement,
        "old_value": old_value,
        "observed_on": _observed_on(golden).isoformat(),
        "replay_value": candidate or "unknown (the replay did not observe it)",
        "search_results": evidence,
    }
    value, usd, failure = await _compare(client, "nano", inputs, old_value, evidence)
    if value is None or value.confidence < CHANGED_CONFIDENCE:
        # Cascade rule applied to the fact check: an unsure or ungrounded Nano answer gets a
        # Super second opinion (in live runs Nano flipped on identical evidence at temperature 0).
        second, extra, failure2 = await _compare(client, "super", inputs, old_value, evidence)
        usd += extra
        if second is not None and (value is None or second.confidence >= value.confidence):
            value, failure = second, failure2
    top = results.hits[0] if results.hits else None
    changed = bool(value and value.changed)
    return DriftCheck(
        **base,
        tavily_results_ref=results.ref,
        changed=changed,
        new_value=value.new_value if value and value.changed else None,
        confidence=value.confidence if value else 0.0,
        statement=fact.statement,
        source_url=top.url if top else None,
        source_title=top.title if top else None,
        ruling=_compare_ruling(fact, value, failure),
    ), usd


_UNGROUNDED_CONFIDENCE = 0.3


async def _compare(
    client: ChatClient, tier: Tier, inputs: dict[str, str], old_value: str, evidence: str
) -> tuple[CompareFactOut | None, float, str | None]:
    spec = get_spec("compare_fact")
    result = await call_structured(
        client,
        prompt=spec.name,
        tier=tier,
        inputs=inputs,
        schema=CompareFactOut,
        max_tokens=spec.max_tokens,
    )
    return _grounded_compare(result.value, old_value, evidence), result.usd, result.failure_code


def _grounded_compare(
    value: CompareFactOut | None, old_value: str, evidence: str
) -> CompareFactOut | None:
    """Keep Nano's comparison only when the evidence backs it (in live runs Nano answered
    "unchanged, 0.95" when the results stated no value at all). "Changed" needs the new
    value in the evidence; "unchanged" needs the old value there; otherwise the answer is
    kept but its confidence is capped, so it can neither make a pair drift nor clear it."""
    if value is None:
        return None
    if value.changed:
        grounded = bool(value.new_value) and value_in(str(value.new_value), evidence)
        if value.new_value and not value_in(str(value.new_value), old_value) and grounded:
            return value
        return value.model_copy(update={"changed": False, "confidence": _UNGROUNDED_CONFIDENCE})
    if value_in(old_value, evidence):
        return value
    return value.model_copy(update={"confidence": min(value.confidence, _UNGROUNDED_CONFIDENCE)})


def _compare_ruling(fact: WorldFact, value: CompareFactOut | None, code: str | None) -> str:
    if value is None:
        return copy.reason_text(code or "schema_failure")
    if value.changed and value.confidence >= CHANGED_CONFIDENCE:
        return f"Changed: {fact.statement.rstrip('.')}; now {value.new_value or 'different'}."
    if value.confidence >= CHANGED_CONFIDENCE:
        return f"The world did not move: {fact.statement.rstrip('.')}."
    # Never claim certainty the evidence does not give: uncertainty is never hidden.
    return "Could not confirm the current value from the search results."


_OBS_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _query_for(call: ToolCall) -> str:
    args = call.args_norm
    if call.tool == "finance.rate":
        return f"{args.get('base', '')} to {args.get('quote', '')} exchange rate today".strip()
    if call.tool == "web.search":
        return str(args.get("query") or "")
    words = [str(v) for k, v in sorted(args.items()) if isinstance(v, str) and k != "date"]
    return current_query(" ".join([call.tool.replace(".", " "), *words]))


def observed_facts(golden: Trace, replay: Trace) -> list[WorldFact]:
    """Facts read straight off the traces when bless-time extraction found none: a
    world-dependent tool called with identical arguments in both runs whose result changed
    (golden ``finance.rate`` 1.08, replay 1.1196) is the world moving, observed directly.
    Tavily still has to confirm it before the pair can become ``drift``."""
    replayed = {
        (s.tool, s.args_hash): s for s in replay.steps if isinstance(s, ToolCall) and s.result_hash
    }
    facts: list[WorldFact] = []
    for step in golden.steps:
        spec = get_tool(step.tool) if isinstance(step, ToolCall) else None
        if not isinstance(step, ToolCall) or spec is None or not spec.world_dependent:
            continue
        twin = replayed.get((step.tool, step.args_hash))
        if step.tool == "time.now" or twin is None or twin.result_hash == step.result_hash:
            continue
        old = _OBS_NUMBER.findall(step.result_preview)
        new = _OBS_NUMBER.findall(twin.result_preview)
        moved = [o for o, n in zip(old, new, strict=False) if o != n]
        if not moved:
            continue
        facts.append(
            WorldFact(
                fact_id=f"obs_{step.step_id}",
                statement=f"{step.tool} returned {moved[0]} on {_observed_on(golden).isoformat()}",
                source_step=step.step_id,
                volatile=True,
                query_hint=_query_for(step),
                value=moved[0],
            )
        )
    return facts


def replay_value(fact: WorldFact, golden: Trace, replay: Trace) -> str | None:
    """The value of ``fact`` as the replay's own world tool reported it: the same tool call
    (identical args) in both runs, the number at the position where the golden result held
    the fact's value. ``None`` when the replay did not observe it."""
    if not fact.value:
        return None
    twins = {(s.tool, s.args_hash): s for s in replay.steps if isinstance(s, ToolCall)}
    for step in golden.steps:
        if not isinstance(step, ToolCall) or (
            fact.source_step and step.step_id != fact.source_step
        ):
            continue
        twin = twins.get((step.tool, step.args_hash))
        if twin is None:
            continue
        old = _OBS_NUMBER.findall(step.result_preview)
        new = _OBS_NUMBER.findall(twin.result_preview)
        for i, number in enumerate(old):
            if i < len(new) and value_in(number, fact.value) and value_in(fact.value, number):
                return new[i] if new[i] != number else None
    return None
