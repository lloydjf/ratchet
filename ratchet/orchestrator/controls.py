"""drifting → controlling → deciding: control replays for contested pairs (ADR 0023).

A single replay cannot tell nondeterminism from a regression: Token Factory ignores
``seed``, so an *unchanged* skill sometimes answers differently. After judging, every pair
that is **contested** gets one more replay of its golden with the edit's **parent** tree
(the unchanged skill on ``main``) — the *control* — judged against the golden by Level 0
and the Nano screen only (``CascadeConfig(mode="nano_only")``; never Super or Ultra).
Contested (:func:`contested`): ``regression`` or ``uncertain``, not decided by Level 0, no
judge or infra error; or (:func:`hard_contested`, ADR 0023 second amendment) a Level 0
hard fail whose every new hard-class tool the **parent** skill text already instructs
(:func:`instructed`). A hard call the parent skill never names (the edit introduced
``github.close_issue``) keeps the hard fail without a control.

Decision rule (:func:`apply_control`, pure):

- a contested hard fail: the control diverges only when its own Level 0 diff has a new
  hard-class call for every (tool, side-effect class) the pair's has; then ``pass`` as
  below, the Level 0 finding stays attached as evidence; otherwise the hard fail stands;
- the control also differs (Level 0 shows a difference on the same tool and side-effect
  class as the pair, or the Nano screen / a Level 0 hard fail makes it a ``regression``)
  → the pair becomes ``pass`` ("same result, noisy task"), ``decided_by="control"``, the
  nondeterminism reason, ``error_code=None``, ``control_outcome="diverges"``;
- the control passes → the outcome stands, ``control_outcome="agrees"`` strengthens it;
- anything else (the control could not run, Nano unsure) → the outcome stands, no
  ``control_outcome``. Only a diverging control turns a contested pair into ``pass``.

Control batches use ``ctrl_<hex>_a<n>_<ii>`` ids, so they never mix with the edit's
replays. Files (restart-idempotent): ``attempt_<n>/skills-parent.tar.zst``,
``attempt_<n>/controls/<golden>.json`` (the control's own pair verdict, golden vs control)
and ``attempt_<n>/pairs/<golden>.control.json`` (the adjusted pair). A batch with a job id
is never resubmitted and a saved control is never judged again. ``EditOptions.reuse_controls``
(bench re-judge) only uses controls already in the attempt. Each resolved pair emits
``pair.control {golden, replay, control, control_outcome}``. Nothing contested → no replay,
no spend. A control that cannot be dispatched leaves the pair as judged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from ratchet import copy
from ratchet.errors import RatchetError
from ratchet.judge import level0
from ratchet.judge.cascade import CascadeConfig, PairContext, judge_pair
from ratchet.log import get_logger
from ratchet.models import (
    HARD_SIDE_EFFECTS,
    Level0Finding,
    PairVerdict,
    ReplayBatch,
    ToolCall,
    Trace,
)
from ratchet.orchestrator.context import EditContext
from ratchet.orchestrator.ids import control_batch_id_for
from ratchet.orchestrator.journal import emit
from ratchet.orchestrator.judging import (
    current_pair,
    failed_pair,
    load_pair,
    pair_path,
    uncertain_pair,
)
from ratchet.orchestrator.replaying import (
    collect_batches,
    plan_batches,
    sandbox_refusal,
    submit_prepared,
)
from ratchet.replay.packing import pack_batch_inputs
from ratchet.store import BatchRow
from ratchet.store.base import write_json_atomic
from ratchet.tools.catalog import wire_name

__all__ = [
    "CONTROL_CASCADE",
    "ControlOutcome",
    "contested",
    "hard_contested",
    "hard_tools",
    "instructed",
    "parent_skill_texts",
    "controls_enabled",
    "control_outcome",
    "apply_control",
    "control_path",
    "control_batches",
    "handle_controlling",
]

ControlOutcome = Literal["agrees", "diverges"]
CONTROL_CASCADE = CascadeConfig(mode="nano_only")
_PARENT_TAR = "skills-parent.tar.zst"
_log = get_logger("orchestrator")

Found = dict[str, tuple[Trace, list[ToolCall]] | str]  # golden -> (control, unexpected) | code


# --- the rule (pure) --------------------------------------------------------------------------
def contested(pair: PairVerdict) -> bool:
    """A judged pair a control replay can settle: a judgement against the edit, not an
    infrastructure or judge failure (those keep their own WARN reason). A pair the drift
    check could not reach (``drift_unavailable``) is not contested either: with
    Tavily down the edit is never passed, and a diverging control would call world
    drift "nondeterminism"."""
    return (
        pair.outcome in ("regression", "uncertain")
        and pair.decided_by not in ("level0", "control", "infra")
        and pair.error_code is None
        and bool(pair.replay_run_id)
    )


def hard_tools(finding: Level0Finding) -> set[tuple[str, str]]:
    """(tool, side-effect class) of every new hard-class call in a Level 0 finding."""
    calls = [c for c in finding.new_calls if c.side_effect in HARD_SIDE_EFFECTS]
    return {(c.tool, c.side_effect) for c in calls}


def instructed(tool: str, skill_text: str) -> bool:
    """Does the skill text tell the agent to call ``tool``? Case-insensitive substring of the
    dotted name (``github.comment``) or its wire name (``github__comment``); only when neither
    appears, the last segment in backticks (`` `comment` ``). Prose ("do not close") is not."""
    text, dotted = skill_text.lower(), tool.lower()
    if dotted in text or wire_name(dotted) in text:
        return True
    return f"`{dotted.rsplit('.', 1)[-1]}`" in text


def _level0_hard(pair: PairVerdict) -> bool:
    return pair.decided_by == "level0" and pair.level0.hard_fail


def hard_contested(pair: PairVerdict, skill_texts: Sequence[str]) -> bool:
    """ADR 0023 second amendment: a Level 0 hard fail gets a control when every new
    hard-class tool is already instructed by the parent skill (agent variance, not the edit).
    ``skill_texts`` are the parent commit's SKILL.md of the affected skills."""
    tools = hard_tools(pair.level0)
    return (
        _level0_hard(pair)
        and pair.outcome == "regression"
        and pair.error_code is None
        and bool(pair.replay_run_id)
        and bool(tools)
        and all(any(instructed(tool, text) for text in skill_texts) for tool, _ in tools)
    )


def parent_skill_texts(ctx: EditContext) -> list[str]:
    """The parent commit's ``<skill>/SKILL.md`` of each affected skill (sync, git)."""
    texts: list[str] = []
    for skill in ctx.edit.affected_skills:
        raw = ctx.repo.read_file(ctx.edit.parent, f"{skill}/SKILL.md")
        if raw is not None:
            texts.append(raw.decode("utf-8", errors="replace"))
    return texts


def controls_enabled(ctx: EditContext) -> bool:
    return ctx.settings.ratchet_feature_control_replays


def _signals(finding: Level0Finding) -> set[tuple[str, str]]:
    calls = [*finding.new_calls, *finding.missing_calls, *(g for g, _ in finding.changed_args)]
    return {(c.tool, c.side_effect) for c in calls}


def control_outcome(pair: PairVerdict, control: PairVerdict) -> ControlOutcome | None:
    """``diverges`` when the unchanged skill also differs from the golden, ``agrees`` when it
    matches it, ``None`` when the control says nothing (failed, or Nano unsure). For a Level 0
    hard fail only the same new hard-class calls count: every (tool, class) of the pair's
    must reappear among the control's new hard-class calls."""
    if control.outcome == "failed" or not control.replay_run_id:
        return None
    if _level0_hard(pair):
        return "diverges" if hard_tools(pair.level0) <= hard_tools(control.level0) else "agrees"
    if _signals(pair.level0) & _signals(control.level0) or control.outcome == "regression":
        return "diverges"
    return "agrees" if control.outcome == "pass" else None


def apply_control(pair: PairVerdict, control: PairVerdict) -> PairVerdict:
    found = control_outcome(pair, control)
    update: dict[str, object] = {
        "control_run_id": control.replay_run_id or None,
        "control_outcome": found,
        "usd": round(pair.usd + control.usd, 8),
    }
    if found == "diverges":
        # Positive evidence, not missing evidence: the unchanged skill differs from the golden
        # on this task too, so the edit is not shown to be at fault. Live E6: with
        # ``uncertain`` here, 8 of 8 unchanged-skill edits were held (ADR 0023 amendment).
        update.update(
            outcome="pass",
            decided_by="control",
            reason=copy.explain_nondeterminism(),
            error_code=None,
        )
    return pair.model_copy(update=update)


# --- files ------------------------------------------------------------------------------------
def control_path(ctx: EditContext, golden_run_id: str) -> Path:
    return ctx.work / "controls" / f"{golden_run_id}.json"


def control_batches(ctx: EditContext) -> list[BatchRow]:
    prefix = control_batch_id_for(ctx.edit_id, ctx.options.attempt, 0)[:-2]
    return [b for b in ctx.store.batches_for_edit(ctx.edit_id) if b.batch_id.startswith(prefix)]


def _judged(ctx: EditContext) -> list[PairVerdict]:
    """The pairs as the drift stage left them (drift runs before controls): a pair the world
    explains is settled and never contested."""
    pairs = (
        current_pair(ctx, s.run_id, upto=".drift") for s in ctx.store.load_selection(ctx.edit_id)
    )
    return [p for p in pairs if p is not None]


# --- replaying the controls -------------------------------------------------------------------
def _prepare(ctx: EditContext, run_ids: list[str]) -> list[tuple[ReplayBatch, Path]]:
    ctx.work.mkdir(parents=True, exist_ok=True)
    tar = ctx.work / _PARENT_TAR
    if not tar.is_file():
        ctx.repo.checkout_tar(ctx.edit.parent, tar)
    attempt = ctx.options.attempt
    specs = plan_batches(
        ctx,
        run_ids,
        tar,
        commit=ctx.edit.parent,
        batch_id=lambda i: control_batch_id_for(ctx.edit_id, attempt, i),
    )
    known = {b.batch_id for b in control_batches(ctx) if b.job_id}
    prepared: list[tuple[ReplayBatch, Path]] = []
    for spec in specs:
        if spec.batch_id in known:
            continue
        inputs = ctx.store.batch_dir(spec.batch_id) / "inputs"
        pack_batch_inputs(ctx.store, spec, inputs, skills_src=tar)
        prepared.append((spec, inputs))
    return prepared


async def _replay(ctx: EditContext, run_ids: list[str]) -> None:
    """Dispatch (once) and collect the control batches; failures leave pairs as judged."""
    if sandbox_refusal(ctx.settings) is not None:
        _log.warning("orchestrator.controls_refused", edit_id=ctx.edit_id)
        return
    try:
        prepared = await asyncio.to_thread(_prepare, ctx, run_ids)
    except RatchetError as exc:
        _log.warning("orchestrator.controls_failed", edit_id=ctx.edit_id, code=exc.code)
        return
    if any(sandbox_refusal(ctx.settings, spec.harness) for spec, _ in prepared):
        _log.warning("orchestrator.controls_refused", edit_id=ctx.edit_id)
        return
    key, runner = ctx.services.pick(ctx.options.runner)
    if not await submit_prepared(ctx, key, runner, prepared):
        _log.warning("orchestrator.controls_failed", edit_id=ctx.edit_id, code="submit_failed")
    await collect_batches(ctx, control_batches)


def _found(ctx: EditContext) -> Found:
    store = ctx.store
    found: Found = {}
    for row in control_batches(ctx):
        result = store.load_batch_result(row.batch_id)
        if result is None:
            spec = store.load_batch_spec(row.batch_id)
            found.update(dict.fromkeys(spec.golden_run_ids, "worker_lost"))
            continue
        found.update(result.failed_goldens)
        for trace in result.replays:
            if trace.golden_of:
                found[trace.golden_of] = (trace, result.unexpected_by_run.get(trace.run_id, []))
    return found


# --- judging the controls ---------------------------------------------------------------------
async def _judge_control(
    ctx: EditContext, golden: Trace, control: Trace, unexpected: list[ToolCall]
) -> PairVerdict:
    finding = level0.diff(golden, control, unexpected)
    pair_ctx = PairContext(golden, control, unexpected, "", finding)
    try:
        return await judge_pair(pair_ctx, ctx.client, CONTROL_CASCADE)
    except RatchetError as exc:
        _log.warning("orchestrator.control_judge_failed", golden=golden.run_id, code=exc.code)
        return uncertain_pair(pair_ctx, exc.code)


async def _control_for(
    ctx: EditContext, pair: PairVerdict, found: Found, sem: asyncio.Semaphore
) -> PairVerdict | None:
    path = control_path(ctx, pair.golden_run_id)
    saved = await asyncio.to_thread(load_pair, path)
    if saved is not None or ctx.options.reuse_controls:
        return saved
    entry = found.get(pair.golden_run_id)
    if entry is None:
        return None  # never dispatched: the pair stays as judged
    golden = await asyncio.to_thread(ctx.store.load_trace, pair.golden_run_id)
    if isinstance(entry, str):
        control = failed_pair(golden, None, entry)
    else:
        async with sem:
            control = await _judge_control(ctx, golden, *entry)
    await asyncio.to_thread(write_json_atomic, path, control)
    return control


async def _resolve(
    ctx: EditContext, pair: PairVerdict, found: Found, sem: asyncio.Semaphore
) -> None:
    adjusted_path = pair_path(ctx, pair.golden_run_id, ".control")
    if adjusted_path.is_file():
        return
    control = await _control_for(ctx, pair, found, sem)
    if control is None:
        return
    adjusted = apply_control(pair, control)
    await asyncio.to_thread(write_json_atomic, adjusted_path, adjusted)
    ids = {"golden": pair.golden_run_id, "replay": pair.replay_run_id}
    body = {"control": adjusted.control_run_id, "control_outcome": adjusted.control_outcome}
    await emit(ctx, "pair.control", {**ids, **body, "task_label": adjusted.task_label})
    if adjusted.outcome != pair.outcome:
        await emit(ctx, "pair.decided", {**ids, "outcome": adjusted.outcome})


async def handle_controlling(ctx: EditContext) -> str:
    if not controls_enabled(ctx):
        return "deciding"
    judged = await asyncio.to_thread(_judged, ctx)
    texts: list[str] = []
    if any(_level0_hard(p) for p in judged):
        texts = await asyncio.to_thread(parent_skill_texts, ctx)
    pairs = [p for p in judged if contested(p) or hard_contested(p, texts)]
    if not pairs:
        return "deciding"
    missing = [p for p in pairs if not control_path(ctx, p.golden_run_id).is_file()]
    found: Found = {}
    if missing and not ctx.options.reuse_controls:
        # Plan over every contested golden (stable batch ids across restarts); batches that
        # already have a job are skipped, so a restart never pays for a control twice.
        await _replay(ctx, [p.golden_run_id for p in pairs])
        found = await asyncio.to_thread(_found, ctx)
    sem = asyncio.Semaphore(ctx.services.judge_concurrency)
    await asyncio.gather(*(_resolve(ctx, p, found, sem) for p in pairs))
    return "deciding"
