"""deciding → acting → notifying, plus the terminal ``failed_infra`` entry.

- **deciding**: ``decide`` (the agreement table) → ``write_narrative`` (Ultra, deterministic
  fallback) → ``verdict_costs`` → the ``Verdict`` (pre-allocated id, stage timings,
  ``simulated_models``) → ``verdict.decided``.
- **acting**: PASS merges ``edit.commit`` (fast-forward); BLOCK reverts it on candidate;
  WARN holds. A merge conflict turns the verdict into WARN ``merge_conflict`` (held). The
  action and its commit are recorded once (``action.json``) so a restart never acts twice.
  It runs under the per-verdict action lock and re-reads the verdict inside it.
- **notifying**: webhook (never raises) + ``verdict.notified``; a Simulate run also gets
  ``simulate.finished``.
- **failed_infra**: WARN ``replay_infra``, held, deterministic narrative, notified.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from dataclasses import replace

from ratchet import clock
from ratchet.errors import ConflictError, GitError
from ratchet.judge.costs import verdict_costs
from ratchet.judge.decide import DecisionResult, decide, merge_conflict
from ratchet.judge.narrative import (
    NarrativeInput,
    fallback_narrative,
    headline_for,
    write_narrative,
)
from ratchet.log import get_logger
from ratchet.models import PairVerdict, Verdict
from ratchet.orchestrator import prom
from ratchet.orchestrator.context import EditContext
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.orchestrator.journal import emit, stage_timeline
from ratchet.orchestrator.judging import load_pairs
from ratchet.orchestrator.notify import build_payload, notify
from ratchet.orchestrator.replaying import current_batches
from ratchet.orchestrator.retrying import retry_batches
from ratchet.orchestrator.stacking import STACKED_ON_HELD, merge_target
from ratchet.store.base import write_json_atomic

__all__ = [
    "handle_deciding",
    "handle_acting",
    "handle_notifying",
    "handle_failed_infra",
    "build_verdict",
    "refresh_stages",
]

_log = get_logger("orchestrator")
_LOCK_WAIT_S = 30.0


def _narrative_input(
    ctx: EditContext, result: DecisionResult, pairs: list[PairVerdict], n_total: int
) -> NarrativeInput:
    costs = verdict_costs(ctx.store, ctx.edit_id)
    return NarrativeInput(
        decision=result.decision,
        action=result.action,
        skills=list(ctx.edit.affected_skills),
        pairs=pairs,
        cost_by_tier=dict(costs.judge_usd_by_tier),  # the narrative reports judging cost
        reasons=list(result.reasons),
        diff_summary=ctx.edit.summary,
        n_total=n_total,
    )


def build_verdict(
    ctx: EditContext,
    result: DecisionResult,
    pairs: list[PairVerdict],
    *,
    narrative: str,
    headline: str,
) -> Verdict:
    costs = verdict_costs(ctx.store, ctx.edit_id)
    batches = [*current_batches(ctx), *retry_batches(ctx)]
    now = clock.now()
    return Verdict(
        verdict_id=verdict_id_for(ctx.edit_id),
        edit_id=ctx.edit_id,
        pairs=pairs,
        decision=result.decision,
        action=result.action,
        narrative=narrative,
        # cost/tokens are judging only (screen, check, adjudicate, extract, narrative), the
        # same population as the all-Ultra counterfactual; agent replay spend is separate.
        cost=dict(costs.judge_usd_by_tier),
        replay_cost=dict(costs.agent_usd_by_tier),
        duration_s=max(int((now - ctx.edit.detected_at).total_seconds()), 0),
        created_at=now,
        reasons=list(result.reasons),
        headline=headline,
        tokens=dict(costs.judge_tokens_by_tier),
        counterfactual_usd=round(costs.counterfactual_ultra_usd, 8),
        stages=stage_timeline(ctx.store, ctx.edit_id, ctx.work),
        simulated_models=ctx.settings.simulated,
        session_hash=ctx.session_hash,
        affected_skills=list(ctx.edit.affected_skills),
        author=ctx.edit.author,
        runner=", ".join(sorted({b.runner for b in batches})),
        batch_ids=[b.batch_id for b in batches],
    )


async def _announce(ctx: EditContext, verdict: Verdict) -> None:
    counts = Counter(p.outcome for p in verdict.pairs)
    payload: dict[str, object] = {
        "verdict_id": verdict.verdict_id,
        "decision": verdict.decision,
        "action": verdict.action,
        "headline": verdict.headline,
        "affected_skills": verdict.affected_skills,
        "reasons": verdict.reasons,
        "n_by_outcome": dict(counts),
        "usd_by_tier": verdict.cost,
        "duration_s": verdict.duration_s,
    }
    await emit(ctx, "verdict.decided", payload, ref=verdict.verdict_id)
    prom.observe_verdict(verdict)


def _decided_marker(ctx: EditContext) -> bool:
    return (ctx.work / "decided.json").is_file() and ctx.store.verdict_path(
        verdict_id_for(ctx.edit_id)
    ).is_file()


async def handle_deciding(ctx: EditContext) -> str:
    if await asyncio.to_thread(_decided_marker, ctx):
        return "acting"
    pairs = await asyncio.to_thread(load_pairs, ctx)
    n_total = len(await asyncio.to_thread(ctx.store.load_selection, ctx.edit_id))
    n_failed = sum(p.outcome == "failed" for p in pairs) + max(n_total - len(pairs), 0)
    timed_out = any(p.error_code == "judge_timeout" for p in pairs)
    result = decide(pairs, n_failed=n_failed, n_total=n_total, ultra_timed_out=timed_out)
    inp = await asyncio.to_thread(_narrative_input, ctx, result, pairs, n_total)
    story = await write_narrative(inp, ctx.client)
    verdict = await asyncio.to_thread(
        build_verdict, ctx, result, pairs, narrative=story.text, headline=headline_for(inp)
    )
    await asyncio.to_thread(ctx.store.save_verdict, verdict)
    await asyncio.to_thread(
        write_json_atomic, ctx.work / "decided.json", {"verdict_id": verdict.verdict_id}
    )
    await asyncio.to_thread(
        ctx.store.set_edit_state, ctx.edit_id, "deciding", verdict_id=verdict.verdict_id
    )
    await _announce(ctx, verdict)
    return "acting"


# --- acting -----------------------------------------------------------------------------------
def _held_for_conflict(ctx: EditContext, verdict: Verdict, code: str) -> Verdict:
    result = merge_conflict()
    if code != "merge_conflict":
        result = DecisionResult("WARN", "held", [code])
    inp = NarrativeInput(
        decision=result.decision,
        action=result.action,
        skills=list(verdict.affected_skills),
        pairs=verdict.pairs,
        cost_by_tier=dict(verdict.cost),
        reasons=list(result.reasons),
        diff_summary=ctx.edit.summary,
        n_total=len(verdict.pairs),
    )
    return verdict.model_copy(
        update={
            "decision": result.decision,
            "action": result.action,
            "reasons": list(result.reasons),
            "narrative": fallback_narrative(inp),
            "headline": headline_for(inp),
        }
    )


def _prior_action(ctx: EditContext, action: str) -> str | None:
    """The commit of this action if any attempt already took it ("" for a hold)."""
    for path in sorted(ctx.store.edit_dir(ctx.edit_id).glob("attempt_*/action.json")):
        done = json.loads(path.read_text(encoding="utf-8"))
        if done.get("action") == action and action != "held":
            return str(done.get("commit") or "")
        if path.parent == ctx.work and done.get("action") == action:
            return str(done.get("commit") or "")
    return None


def _act(ctx: EditContext, verdict: Verdict) -> tuple[Verdict, str | None]:
    """Run the git action once; returns the (possibly downgraded) verdict and the commit."""
    record = ctx.work / "action.json"
    prior = _prior_action(ctx, verdict.action)
    if prior is not None:
        write_json_atomic(record, {"action": verdict.action, "commit": prior or None})
        return verdict, prior or None
    commit: str | None = None
    try:
        if verdict.action == "merged":
            target = merge_target(ctx.store, ctx.repo, ctx.edit)
            if target is None:
                raise ConflictError("an earlier held edit is on candidate", code=STACKED_ON_HELD)
            commit = ctx.repo.merge_candidate(verdict.verdict_id, commit=target)
        elif verdict.action == "reverted":
            commit = ctx.repo.revert(ctx.edit.commit)
    except (ConflictError, GitError) as exc:
        _log.warning("orchestrator.action_failed", verdict_id=verdict.verdict_id, code=exc.code)
        if verdict.decision == "PASS":
            verdict = _held_for_conflict(ctx, verdict, exc.code)
        else:
            reasons = [*verdict.reasons, exc.code]
            verdict = verdict.model_copy(update={"action": "held", "reasons": reasons})
    write_json_atomic(record, {"action": verdict.action, "commit": commit})
    return verdict, commit


def refresh_stages(ctx: EditContext) -> None:
    path = ctx.store.verdict_path(verdict_id_for(ctx.edit_id))
    if path.is_file():
        verdict = ctx.store.load_verdict(verdict_id_for(ctx.edit_id))
        stages = stage_timeline(ctx.store, ctx.edit_id, ctx.work)
        ctx.store.save_verdict(verdict.model_copy(update={"stages": stages}))


def _acquire(ctx: EditContext, verdict_id: str) -> None:
    """Take the per-verdict action lock, waiting out a user action already in flight."""
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            ctx.store.acquire_action_lock(verdict_id)
            return
        except ConflictError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _act_locked(ctx: EditContext) -> Verdict:
    """Run the automatic action under the same lock as Rollback/Approve, re-reading the
    verdict inside it so a concurrent user action is never overwritten."""
    verdict_id = verdict_id_for(ctx.edit_id)
    _acquire(ctx, verdict_id)
    try:
        verdict = ctx.store.load_verdict(verdict_id)
        if verdict.approved_at is not None or verdict.rolled_back_at is not None:
            commit = verdict.action_commit  # a user already acted; keep their result
        else:
            verdict, commit = _act(ctx, verdict)
        stages = stage_timeline(ctx.store, ctx.edit_id, ctx.work)
        verdict = verdict.model_copy(update={"action_commit": commit, "stages": stages})
        ctx.store.save_verdict(verdict)
        return verdict
    finally:
        ctx.store.release_action_lock(verdict_id)


async def handle_acting(ctx: EditContext) -> str:
    verdict = await asyncio.to_thread(_act_locked, ctx)
    payload = {
        "verdict_id": verdict.verdict_id,
        "action": verdict.action,
        "commit": verdict.action_commit,
        "affected_skills": list(verdict.affected_skills),
    }
    await emit(
        ctx, "verdict.acted", {**payload, "decision": verdict.decision}, ref=verdict.verdict_id
    )
    return "notifying"


# --- notifying --------------------------------------------------------------------------------
def _mark_session(ctx: EditContext, status: str) -> None:
    for row in ctx.store.sessions():
        if row.edit_id == ctx.edit_id:
            verdict_id = verdict_id_for(ctx.edit_id)
            ctx.store.upsert_session(replace(row, status=status, verdict_id=verdict_id))


async def handle_notifying(ctx: EditContext) -> str:
    verdict = await asyncio.to_thread(ctx.store.load_verdict, verdict_id_for(ctx.edit_id))
    payload = build_payload(verdict, ctx.edit, ctx.settings.ratchet_public_url)
    delivered = await notify(payload, ctx.settings, client=ctx.services.notify_http)
    body = {"verdict_id": verdict.verdict_id, "delivered": delivered, "text": payload["text"]}
    await emit(ctx, "verdict.notified", body, ref=verdict.verdict_id)
    if ctx.options.scenario_id is not None:
        await asyncio.to_thread(_mark_session, ctx, "done")
        sim: dict[str, object] = {
            "scenario": ctx.options.scenario_id,
            "session_hash": ctx.session_hash,
            "verdict_id": verdict.verdict_id,
            "status": "done",
            "decision": verdict.decision,
        }
        await emit(ctx, "simulate.finished", sim, ref=verdict.verdict_id)
    return "done"


# --- failed_infra -----------------------------------------------------------------------------
async def handle_failed_infra(ctx: EditContext) -> None:
    """Terminal: WARN ``replay_infra``, held, a deterministic narrative, notified once."""
    if await asyncio.to_thread(ctx.store.verdict_path(verdict_id_for(ctx.edit_id)).is_file):
        return
    pairs = await asyncio.to_thread(load_pairs, ctx)
    n_total = len(await asyncio.to_thread(ctx.store.load_selection, ctx.edit_id))
    state = await asyncio.to_thread(ctx.store.edit_state, ctx.edit_id)
    extra = [state.error_code] if state.error_code and state.error_code != "replay_infra" else []
    result = DecisionResult("WARN", "held", ["replay_infra", *extra])
    inp = await asyncio.to_thread(_narrative_input, ctx, result, pairs, n_total)
    verdict = await asyncio.to_thread(
        build_verdict,
        ctx,
        result,
        pairs,
        narrative=fallback_narrative(inp),
        headline=headline_for(inp),
    )
    await asyncio.to_thread(ctx.store.save_verdict, verdict)
    await asyncio.to_thread(
        ctx.store.set_edit_state, ctx.edit_id, "failed_infra", verdict_id=verdict.verdict_id
    )
    await _announce(ctx, verdict)
    payload = build_payload(verdict, ctx.edit, ctx.settings.ratchet_public_url)
    await notify(payload, ctx.settings, client=ctx.services.notify_http)
    if ctx.options.scenario_id is not None:
        await asyncio.to_thread(_mark_session, ctx, "failed")
        sim: dict[str, object] = {
            "scenario": ctx.options.scenario_id,
            "session_hash": ctx.session_hash,
            "verdict_id": verdict.verdict_id,
            "status": "failed",
        }
        await emit(ctx, "simulate.finished", sim, ref=verdict.verdict_id)
