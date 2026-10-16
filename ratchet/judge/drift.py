"""World-drift disambiguation: did the world move, or did the skill break?

Runs only for pairs whose golden is ``world_dependent`` and whose cascade outcome is
``regression`` or ``uncertain`` (never on Level 0 hard fails: a new destructive call is not
explained by a changed fact). For up to four volatile ``WorldFact``s:

1. Tavily ``search(query_hint, search_depth="advanced", ...)`` with **dynamic filtering by the
   claim's domain and date**:
   - domain: when the fact came from a tool step with a URL (``web.fetch`` args, a URL in the
     result preview or the statement), the search is restricted with
     ``include_domains=[that domain]``; if the restricted search finds nothing it is repeated
     unrestricted, so a narrow source never hides the answer.
   - date: ``time_range="week"`` while the claim is at most 7 days old; for older
     claims ``start_date=<observation date>`` so every result since the claim is in range.
   - topic: ``finance`` for facts from ``finance.*`` tools, else ``general``.
2. Nano (``compare_fact.md``, an addition to the prompt set) compares the old value with the
   results: ``changed``, ``new_value``, ``confidence``.
3. Facts changed with confidence ≥ 0.75 go to Super (``attribute_drift.md``). The pair becomes
   ``drift`` only if Super says ``attributable`` with confidence ≥ 0.75 (the confidence floor
   is our choice: a drift outcome lets the edit PASS, so it must be earned).
4. Otherwise the outcome stands, with the checks attached as evidence the world did not move.

A Tavily failure gives ``DriftCheck(status="unavailable")``; the outcome stays and the pair
carries ``error_code="drift_unavailable"``. Unknown never becomes ``drift``.

Steps 1-2 (search and compare, one fact at a time) live in :mod:`ratchet.judge.facts`; this
module re-exports their public names.
"""

from __future__ import annotations

import asyncio

from ratchet import copy
from ratchet.judge.facts import (
    CHANGED_CONFIDENCE,
    check_fact,
    current_query,
    fact_domain,
    observed_facts,
    replay_value,
    search_params,
)
from ratchet.judge.facts import _compare as _compare
from ratchet.judge.facts import _grounded_compare as _grounded_compare
from ratchet.judge.facts import _results_text as _results_text
from ratchet.judge.facts import _search as _search
from ratchet.judge.prompts import get_spec
from ratchet.judge.schemas import AttributeDriftOut
from ratchet.judge.structured import ChatClient, call_structured
from ratchet.judge.tavily import (
    FixtureTavily,
    LiveTavily,
    SearchParams,
    TavilySource,
)
from ratchet.log import get_logger
from ratchet.models import DriftCheck, PairVerdict, Trace

__all__ = [
    "observed_facts",
    "TavilySource",
    "LiveTavily",
    "FixtureTavily",
    "SearchParams",
    "CHANGED_CONFIDENCE",
    "ATTRIBUTABLE_CONFIDENCE",
    "MAX_FACTS",
    "search_params",
    "fact_domain",
    "current_query",
    "replay_value",
    "check_fact",
    "check_pair",
]

ATTRIBUTABLE_CONFIDENCE = 0.75
MAX_FACTS = 4
_log = get_logger("judge.drift")


def _changed_text(checks: list[DriftCheck]) -> str:
    return "\n".join(
        f"- {c.statement} | old: {c.old_value} | new: {c.replay_value or c.new_value}"
        f" (public sources: {c.new_value}) | {c.source_url or '-'}"
        for c in checks
    )


async def _attribute(
    changed: list[DriftCheck], golden: Trace, replay: Trace, client: ChatClient
) -> tuple[bool, float]:
    spec = get_spec("attribute_drift")
    result = await call_structured(
        client,
        prompt=spec.name,
        tier=spec.tier,
        inputs={
            "changed_facts": _changed_text(changed),
            "golden_output": golden.final_output,
            "replay_output": replay.final_output,
        },
        schema=AttributeDriftOut,
        max_tokens=spec.max_tokens,
    )
    value = result.value
    ok = value is not None and value.attributable and value.confidence >= ATTRIBUTABLE_CONFIDENCE
    return ok, result.usd


def _eligible(pair: PairVerdict, golden: Trace) -> bool:
    return (
        golden.world_dependent
        and pair.outcome in ("regression", "uncertain")
        and pair.decided_by != "level0"
    )


async def check_pair(
    pair: PairVerdict,
    golden: Trace,
    replay: Trace,
    client: ChatClient,
    tavily: TavilySource,
) -> PairVerdict:
    facts = [f for f in golden.world_facts if f.volatile][:MAX_FACTS]
    if not facts:
        facts = observed_facts(golden, replay)[:MAX_FACTS]
    if not _eligible(pair, golden) or not facts:
        return pair
    found = await asyncio.gather(*(check_fact(f, golden, replay, client, tavily) for f in facts))
    checks = [c for c, _ in found]
    usd = pair.usd + sum(u for _, u in found)
    changed = [c for c in checks if c.changed and c.confidence >= CHANGED_CONFIDENCE]
    attributable = False
    if changed:
        attributable, attr_usd = await _attribute(changed, golden, replay, client)
        usd += attr_usd
        for i, check in enumerate(checks):
            if check in changed:
                ruling = (
                    "The new output follows from this change."
                    if attributable
                    else ("Changed, but the difference in the output does not follow from it.")
                )
                checks[i] = check.model_copy(
                    update={"attributable": attributable, "ruling": ruling}
                )
    unavailable = any(c.status == "unavailable" for c in checks)
    _log.info(
        "judge.drift",
        golden=golden.run_id,
        replay=replay.run_id,
        n_facts=len(checks),
        n_changed=len(changed),
        attributable=attributable,
        unavailable=unavailable,
    )
    update: dict[str, object] = {"drift": checks, "usd": round(usd, 8)}
    if attributable:
        update.update(
            outcome="drift", decided_by="drift", reason=copy.explain_drift(changed[0].statement)
        )
    elif unavailable and pair.error_code is None:
        update["error_code"] = "drift_unavailable"
    return pair.model_copy(update=update)
