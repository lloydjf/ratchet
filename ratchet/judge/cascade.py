"""The judge cascade for one golden/replay pair: Level 0 → Nano → Super → Ultra.

Thresholds:

- Level 0 hard fail → ``regression``, decided without a model; no tier is ever called.
- Nano ``equivalent`` ≥ 0.85 → ``pass``; anything else goes to Super (never block on Nano).
  A Level 0 ``missing_hard`` call or a changed hard-class call removes the Nano shortcut —
  that is the "weight" those signals carry.
- Super ``equivalent`` ≥ 0.80 → ``pass``; ``improved`` ≥ 0.80 → ``improved``; everything else,
  including ``regressed`` ≥ 0.80, goes to Ultra.
- Ultra (thinking on, reasoning budget) is final, except that an Ultra ``equivalent`` or
  ``improved`` against a Super ``regressed`` ≥ 0.80 makes the pair ``uncertain`` (contested).

Design decision: Level 2 could read "regressed ≥ 0.80 and Level 1 also regressed →
regression", but the agreement table only BLOCKs when Super and Ultra agree, and the
regression scenario expects "Nano flags, Super and Ultra agree". So a Super
``regressed`` ≥ 0.80 is always forwarded to Ultra for confirmation; the pair becomes
``regression`` (``decided_by="ultra"``) only when Ultra says ``regressed``.

Failures never pass a pair: a failed judge call is an ``uncertain`` opinion with the error
code as its reason and the pair moves on; an Ultra timeout makes the pair ``uncertain`` with
``error_code="judge_timeout"``. Cited ``evidence_steps`` that are not in either trace are
dropped and the drop is noted in the opinion's reasons. Drift is not checked here.

Opinion construction (``valid_opinion``, ``clean_reasons``, ``short_failure``) lives in
:mod:`ratchet.judge.opinions` and is re-exported here.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from ratchet import copy
from ratchet.config.settings import get_settings
from ratchet.judge import summaries
from ratchet.judge.opinions import clean_reasons, short_failure, valid_opinion
from ratchet.judge.prompts import get_spec
from ratchet.judge.schemas import AdjudicateOut, CheckOut, ScreenOut
from ratchet.judge.structured import ChatClient, call_structured
from ratchet.log import get_logger
from ratchet.models import (
    HARD_SIDE_EFFECTS,
    JudgeOpinion,
    Level0Finding,
    PairOutcome,
    PairVerdict,
    Tier,
    ToolCall,
    Trace,
)

__all__ = [
    "OpinionHook",
    "CascadeConfig",
    "PairContext",
    "judge_pair",
    "NANO_PASS",
    "SUPER_DECIDE",
    "valid_opinion",
    "clean_reasons",
    "short_failure",
    "failure_code",
]

NANO_PASS = 0.85
# Below this golden-vs-replay final-output similarity the Nano screen may not clear a pair
# alone (ADR 0024): on the 2026-10-16 sweep, 90 % of true passes scored 1.0 and the
# regressions Nano cleared alone had a median of 0.92.
OUTPUT_SIMILARITY_FLOOR = 0.90
NANO_REGRESSED = 0.90  # forwarded either way; kept for the record
SUPER_DECIDE = 0.80
_PASS_THRESHOLD: dict[Tier, float] = {"nano": NANO_PASS, "super": SUPER_DECIDE, "ultra": 0.0}
_CODE = re.compile(r"[a-z][a-z0-9_]*")

_log = get_logger("judge")

Mode = Literal["cascade", "nano_only", "super_only", "ultra_only"]
DecidedBy = Literal["level0", "nano", "super", "ultra", "drift", "infra"]


@dataclass(frozen=True)
class CascadeConfig:
    mode: Mode = "cascade"
    level0: bool = True  # E5: level0 off → no hard-fail shortcut, no Level 0 evidence
    drift: bool = True  # E3: consumed by the orchestrator (drift.check_pair), not here
    reasoning_budget: int | None = None  # default: settings.ratchet_ultra_reasoning_budget


@dataclass(frozen=True)
class PairContext:
    golden: Trace
    replay: Trace
    unexpected: list[ToolCall]
    skill_diff: str
    level0: Level0Finding


def _level0_note(finding: Level0Finding) -> str:
    notes = [f"missing {c.tool} ({c.side_effect}) {c.step_id}" for c in finding.missing_calls]
    notes += [f"changed args {g.tool}: {g.step_id} -> {r.step_id}" for g, r in finding.changed_args]
    notes += [f"new {c.tool} ({c.side_effect}) {c.step_id}" for c in finding.new_calls]
    return f"\nLevel 0 (deterministic): {'; '.join(notes)}" if notes else ""


def _weighted(finding: Level0Finding) -> bool:
    """Level 0 signals that forbid a Nano-only pass (ADR 0024): any structural difference
    (a missing, new or re-argued side-effect call) or a materially different final output.
    On the first live sweep every R3 (tool-argument) pair Nano cleared alone carried a
    Level 0 "changed args" finding, and no cosmetic-edit pair did."""
    structural = bool(finding.missing_calls or finding.changed_args or finding.new_calls)
    return structural or finding.output_similarity < OUTPUT_SIMILARITY_FLOOR


class _Judges:
    def __init__(self, ctx: PairContext, client: ChatClient, config: CascadeConfig) -> None:
        self.ctx, self.client, self.config = ctx, client, config
        self.ids = summaries.step_ids(ctx.golden, ctx.replay, extra=ctx.unexpected)
        self.note = _level0_note(ctx.level0) if config.level0 else ""

    def _common(self) -> dict[str, str]:
        g, r = self.ctx.golden, self.ctx.replay
        return {
            "task_input": g.task_input,
            "criteria": summaries.criteria_text(g.success_criteria),
            "golden_output": g.final_output,
            "replay_output": r.final_output,
            "skill_diff": self.ctx.skill_diff or "(no diff available)",
        }

    def _steps(self) -> dict[str, str]:
        return {
            "golden_steps": summaries.step_lines(self.ctx.golden),
            "replay_steps": summaries.step_lines(self.ctx.replay, self.ctx.unexpected) + self.note,
        }

    async def screen(self, tier: Tier = "nano") -> JudgeOpinion:
        spec = get_spec("screen")
        inputs = self._common()
        inputs["golden_calls"] = summaries.call_summary(self.ctx.golden)
        inputs["replay_calls"] = (
            summaries.call_summary(self.ctx.replay, self.ctx.unexpected) + self.note
        )
        result = await call_structured(
            self.client,
            prompt=spec.name,
            tier=tier,
            inputs=inputs,
            schema=ScreenOut,
            max_tokens=spec.max_tokens,
        )
        return valid_opinion(tier, result, self.ids)

    async def check(self, level1: JudgeOpinion | None, tier: Tier = "super") -> JudgeOpinion:
        spec = get_spec("check")
        inputs = {**self._common(), **self._steps()}
        inputs["level1_verdict"] = level1.verdict if level1 else "not consulted"
        inputs["level1_confidence"] = f"{level1.confidence:.2f}" if level1 else "n/a"
        result = await call_structured(
            self.client,
            prompt=spec.name,
            tier=tier,
            inputs=inputs,
            schema=CheckOut,
            max_tokens=spec.max_tokens,
        )
        return valid_opinion(tier, result, self.ids)

    async def adjudicate(
        self, level1: JudgeOpinion | None, level2: JudgeOpinion | None
    ) -> JudgeOpinion:
        spec = get_spec("adjudicate")
        inputs = {**self._common(), **self._steps()}
        inputs["level1"] = summaries.opinion_text(level1)
        inputs["level2"] = summaries.opinion_text(level2)
        budget = self.config.reasoning_budget or get_settings().ratchet_ultra_reasoning_budget
        result = await call_structured(
            self.client,
            prompt=spec.name,
            tier="ultra",
            inputs=inputs,
            schema=AdjudicateOut,
            max_tokens=spec.max_tokens,
            thinking=True,
            reasoning_budget=budget,
        )
        return valid_opinion("ultra", result, self.ids)


# --- outcomes ---------------------------------------------------------------------------------
def _failed_criterion(opinion: JudgeOpinion, met: bool = False) -> str | None:
    return next((c.criterion for c in opinion.criteria_met if c.met is met), None)


def _first_reason(opinion: JudgeOpinion) -> str:
    return opinion.reasons[0] if opinion.reasons else "The judges gave no reason"


def _explain(outcome: PairOutcome, opinion: JudgeOpinion | None, why: str | None = None) -> str:
    if outcome == "pass":
        return copy.explain_pass()
    if opinion is None:
        return copy.explain_uncertain(why or "No judge could decide")
    if outcome == "regression":
        return copy.explain_regression(_failed_criterion(opinion) or _first_reason(opinion))
    if outcome == "improved":
        return copy.explain_improved(_first_reason(opinion))
    code = failure_code(opinion)
    if code is not None:
        why = why or copy.reason_text(code).rstrip(".")
    return copy.explain_uncertain(why or _first_reason(opinion))


def _verdict(
    ctx: PairContext,
    opinions: list[JudgeOpinion],
    outcome: PairOutcome,
    decided_by: DecidedBy,
    reason: str,
    error_code: str | None = None,
) -> PairVerdict:
    verdict = PairVerdict(
        golden_run_id=ctx.golden.run_id,
        replay_run_id=ctx.replay.run_id,
        level0=ctx.level0,
        opinions=opinions,
        drift=[],
        outcome=outcome,
        usd=round(sum(o.usd for o in opinions), 8),
        error_code=error_code,
        task_input=ctx.golden.task_input,
        task_label=ctx.golden.task_label,
        decided_by=decided_by,
        reason=reason,
    )
    _log.info(
        "judge.pair",
        golden=ctx.golden.run_id,
        replay=ctx.replay.run_id,
        outcome=outcome,
        decided_by=decided_by,
        n_opinions=len(opinions),
        error_code=error_code,
    )
    return verdict


def _level0_verdict(ctx: PairContext) -> PairVerdict:
    hard = [c for c in ctx.level0.new_calls if c.side_effect in HARD_SIDE_EFFECTS]
    worst = hard[0].side_effect if hard else "hard-class"
    reason = copy.level0_marker(sum(1 for c in hard if c.side_effect == worst), worst)
    return _verdict(ctx, [], "regression", "level0", reason)


def _single(ctx: PairContext, opinion: JudgeOpinion) -> PairVerdict:
    """E4 single-tier modes: the one judge decides, with its tier's pass threshold."""
    tier = opinion.tier
    outcome: PairOutcome
    if opinion.verdict == "regressed":
        outcome = "regression"
    elif opinion.verdict in ("equivalent", "improved"):
        confident = opinion.confidence >= _PASS_THRESHOLD[tier]
        mapped: PairOutcome = "pass" if opinion.verdict == "equivalent" else "improved"
        outcome = mapped if confident else "uncertain"
    else:
        outcome = "uncertain"
    code = failure_code(opinion)
    return _verdict(ctx, [opinion], outcome, tier, _explain(outcome, opinion), code)


def failure_code(opinion: JudgeOpinion) -> str | None:
    """The error code of an opinion that stands for a failed call (see :func:`valid_opinion`)."""
    if (
        opinion.verdict == "uncertain"
        and opinion.confidence == 0.0
        and not opinion.evidence_steps
        and len(opinion.reasons) == 1
        and _CODE.fullmatch(opinion.reasons[0])
    ):
        return opinion.reasons[0]
    return None


def _resolve_ultra(
    ctx: PairContext, nano: JudgeOpinion | None, sup: JudgeOpinion, ultra: JudgeOpinion
) -> PairVerdict:
    opinions = [o for o in (nano, sup, ultra) if o is not None]
    code = failure_code(ultra)
    if code is not None:
        return _verdict(ctx, opinions, "uncertain", "ultra", _explain("uncertain", ultra), code)
    if ultra.verdict == "regressed":
        if ultra.caused_by_diff is False:
            # Causality: a difference the diff cannot explain is nondeterminism or drift, so
            # it is held for a human, never reverted (live runs blocked 8/8 noise edits without it).
            why = "the skill diff does not explain the difference; likely nondeterminism"
            return _verdict(ctx, opinions, "uncertain", "ultra", copy.explain_uncertain(why))
        criterion = _failed_criterion(ultra) or _failed_criterion(sup) or _first_reason(ultra)
        reason = copy.explain_regression(criterion)
        return _verdict(ctx, opinions, "regression", "ultra", reason)
    if ultra.verdict in ("equivalent", "improved"):
        if sup.verdict == "regressed" and sup.confidence >= SUPER_DECIDE:
            why = "Judges disagreed: Super found a regression and Ultra did not"
            return _verdict(ctx, opinions, "uncertain", "ultra", copy.explain_uncertain(why))
        outcome: PairOutcome = "pass" if ultra.verdict == "equivalent" else "improved"
        return _verdict(ctx, opinions, outcome, "ultra", _explain(outcome, ultra))
    return _verdict(ctx, opinions, "uncertain", "ultra", _explain("uncertain", ultra))


OpinionHook = Callable[[JudgeOpinion], Awaitable[None]]


async def _reported(opinion: JudgeOpinion, hook: OpinionHook | None) -> JudgeOpinion:
    if hook is not None:
        await hook(opinion)
    return opinion


async def judge_pair(
    ctx: PairContext,
    client: ChatClient,
    config: CascadeConfig | None = None,
    *,
    on_opinion: OpinionHook | None = None,
) -> PairVerdict:
    """Run the cascade for one pair. ``on_opinion`` is awaited as each tier answers, so the
    progress strip can light Nano, Super and Ultra up live: fast feedback feels honest."""
    config = config or CascadeConfig()
    if config.level0 and ctx.level0.hard_fail:
        return _level0_verdict(ctx)
    if not ctx.replay.harness_ok:
        code = ctx.replay.error_code or "harness_failed"
        return _verdict(ctx, [], "failed", "infra", copy.explain_failed(code), code)
    judges = _Judges(ctx, client, config)
    if config.mode == "nano_only":
        return _single(ctx, await _reported(await judges.screen(), on_opinion))
    if config.mode == "super_only":
        return _single(ctx, await _reported(await judges.check(None), on_opinion))
    if config.mode == "ultra_only":
        return _single(ctx, await _reported(await judges.adjudicate(None, None), on_opinion))
    nano = await _reported(await judges.screen(), on_opinion)
    shortcut = not (config.level0 and _weighted(ctx.level0))
    if shortcut and nano.verdict == "equivalent" and nano.confidence >= NANO_PASS:
        return _verdict(ctx, [nano], "pass", "nano", copy.explain_pass())
    sup = await _reported(await judges.check(nano), on_opinion)
    if sup.confidence >= SUPER_DECIDE and sup.verdict in ("equivalent", "improved"):
        outcome: PairOutcome = "pass" if sup.verdict == "equivalent" else "improved"
        return _verdict(ctx, [nano, sup], outcome, "super", _explain(outcome, sup))
    ultra = await _reported(await judges.adjudicate(nano, sup), on_opinion)
    return _resolve_ultra(ctx, nano, sup, ultra)
