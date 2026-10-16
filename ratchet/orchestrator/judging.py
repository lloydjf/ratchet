"""judging → drifting → controlling (design §3; ADR 0023: controls run after the
drift check so a world-drift pair is never counted as noise).

Per pair: ``level0.diff`` → ``cascade.judge_pair`` (at most ``judge_concurrency`` pairs at
once). A golden whose replay never produced a trace (``failed_goldens``) becomes
``PairVerdict(outcome="failed", decided_by="infra")``. Every judged pair is saved to
``edits/<id>/attempt_<n>/pairs/<golden>.json`` before its ``pair.decided`` event, so a
restarted pipeline reuses it instead of paying for the cascade again. Control-adjusted
pairs (``<golden>.control.json``, :mod:`ratchet.orchestrator.controls`) and drift results
(``<golden>.drift.json``) are saved beside it; drift wins over control, control over judged.

An unexpected judge failure makes the pair ``uncertain`` with the error code — never
``pass`` (CONTRACTS §0).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from ratchet.errors import RatchetError
from ratchet.judge import level0
from ratchet.judge.cascade import PairContext, judge_pair
from ratchet.judge.drift import check_pair
from ratchet.log import get_logger
from ratchet.models import JudgeOpinion, Level0Finding, PairVerdict, ToolCall, Trace
from ratchet.orchestrator.context import EditContext
from ratchet.orchestrator.journal import emit
from ratchet.orchestrator.replaying import current_batches
from ratchet.orchestrator.retrying import retry_batches
from ratchet.replay.harness.base import same_adapter
from ratchet.store.base import write_json_atomic

__all__ = [
    "Replayed",
    "collect_replays",
    "load_pairs",
    "handle_judging",
    "handle_drifting",
    "failed_pair",
    "uncertain_pair",
    "pair_path",
    "load_pair",
]

_log = get_logger("orchestrator")


@dataclass(frozen=True)
class Replayed:
    golden: Trace
    replay: Trace | None
    unexpected: list[ToolCall]
    error_code: str | None


def collect_replays(ctx: EditContext) -> list[Replayed]:
    """One entry per selected golden, in selection order. A golden the retry round
    (:mod:`ratchet.orchestrator.retrying`) replayed again takes the retry's result in place
    of the crashed one; a retry batch without a result changes nothing."""
    store = ctx.store
    replays: dict[str, Trace] = {}
    unexpected: dict[str, list[ToolCall]] = {}
    failed: dict[str, str] = {}
    for row in current_batches(ctx):
        result = store.load_batch_result(row.batch_id)
        if result is None:
            spec = store.load_batch_spec(row.batch_id)
            failed.update(dict.fromkeys(spec.golden_run_ids, "worker_lost"))
            continue
        for trace in result.replays:
            if trace.golden_of:
                replays[trace.golden_of] = trace
        unexpected.update(result.unexpected_by_run)
        failed.update(result.failed_goldens)
    for row in retry_batches(ctx):
        result = store.load_batch_result(row.batch_id)
        if result is None:
            continue
        for run_id in store.load_batch_spec(row.batch_id).golden_run_ids:
            replays.pop(run_id, None)
            failed.pop(run_id, None)
        for trace in result.replays:
            if trace.golden_of:
                replays[trace.golden_of] = trace
        unexpected.update(result.unexpected_by_run)
        failed.update(result.failed_goldens)
    items: list[Replayed] = []
    for sel in store.load_selection(ctx.edit_id):
        golden = store.load_trace(sel.run_id)
        replay = replays.get(sel.run_id)
        extra = unexpected.get(replay.run_id, []) if replay is not None else []
        items.append(Replayed(golden, replay, extra, failed.get(sel.run_id)))
    return items


def pair_path(ctx: EditContext, golden_run_id: str, suffix: str = "") -> Path:
    return ctx.work / "pairs" / f"{golden_run_id}{suffix}.json"


def load_pair(path: Path) -> PairVerdict | None:
    if not path.is_file():
        return None
    return PairVerdict.model_validate_json(path.read_text(encoding="utf-8"))


PAIR_LAYERS: tuple[str, ...] = (".control", ".drift", "")
"""Pair files in the order the stages write them, latest first: the control-adjusted pair
(ADR 0023, written last) over the drift result over the judged one."""


def current_pair(
    ctx: EditContext, golden_run_id: str, *, upto: str = ".control"
) -> PairVerdict | None:
    """The latest version of a pair, starting the search at layer ``upto``."""
    layers = PAIR_LAYERS[PAIR_LAYERS.index(upto) :]
    for suffix in layers:
        found = load_pair(pair_path(ctx, golden_run_id, suffix))
        if found is not None:
            return found
    return None


def load_pairs(ctx: EditContext) -> list[PairVerdict]:
    """The final pair verdicts in selection order (``PAIR_LAYERS``)."""
    pairs = (current_pair(ctx, sel.run_id) for sel in ctx.store.load_selection(ctx.edit_id))
    return [p for p in pairs if p is not None]


def failed_pair(golden: Trace, replay: Trace | None, code: str) -> PairVerdict:
    from ratchet import copy

    replay_id = replay.run_id if replay is not None else ""
    finding = Level0Finding(
        golden_run_id=golden.run_id,
        replay_run_id=replay_id,
        new_calls=[],
        missing_calls=[],
        changed_args=[],
        hard_fail=False,
    )
    return PairVerdict(
        golden_run_id=golden.run_id,
        replay_run_id=replay_id,
        level0=finding,
        opinions=[],
        drift=[],
        outcome="failed",
        usd=0.0,
        error_code=code,
        task_input=golden.task_input,
        task_label=golden.task_label,
        decided_by="infra",
        reason=copy.explain_failed(code),
    )


def uncertain_pair(pair_ctx: PairContext, code: str) -> PairVerdict:
    from ratchet import copy

    return PairVerdict(
        golden_run_id=pair_ctx.golden.run_id,
        replay_run_id=pair_ctx.replay.run_id,
        level0=pair_ctx.level0,
        opinions=[],
        drift=[],
        outcome="uncertain",
        usd=0.0,
        error_code=code,
        task_input=pair_ctx.golden.task_input,
        task_label=pair_ctx.golden.task_label,
        decided_by="nano",
        reason=copy.explain_uncertain(copy.reason_text(code)),
    )


def _ids(pair: PairVerdict) -> dict[str, object]:
    return {"golden": pair.golden_run_id, "replay": pair.replay_run_id}


async def _save(ctx: EditContext, path: Path, pair: PairVerdict) -> None:
    await asyncio.to_thread(write_json_atomic, path, pair)


async def _cascade(ctx: EditContext, item: Replayed, diff: str) -> PairVerdict:
    assert item.replay is not None
    finding = level0.diff(item.golden, item.replay, item.unexpected)
    await emit(
        ctx,
        "pair.level0",
        {
            "golden": item.golden.run_id,
            "replay": item.replay.run_id,
            "hard_fail": finding.hard_fail,
            "n_new": len(finding.new_calls),
            "n_missing": len(finding.missing_calls),
        },
    )
    pair_ctx = PairContext(item.golden, item.replay, item.unexpected, diff, finding)
    ids = {"golden": item.golden.run_id, "replay": item.replay.run_id}

    async def report(op: JudgeOpinion) -> None:
        # Emitted as each tier answers, not after the pair, so the strip moves tier by tier.
        body = {"tier": op.tier, "verdict": op.verdict, "confidence": op.confidence}
        await emit(ctx, "pair.opinion", {**ids, **body, "usd": op.usd})

    try:
        return await judge_pair(pair_ctx, ctx.client, ctx.services.cascade, on_opinion=report)
    except RatchetError as exc:
        _log.warning("orchestrator.judge_failed", golden=item.golden.run_id, code=exc.code)
        return uncertain_pair(pair_ctx, exc.code)


async def _judge_one(
    ctx: EditContext, item: Replayed, diff: str, sem: asyncio.Semaphore
) -> PairVerdict:
    path = pair_path(ctx, item.golden.run_id)
    existing = await asyncio.to_thread(load_pair, path)
    if existing is not None:
        return existing
    if item.replay is None:
        pair = failed_pair(item.golden, None, item.error_code or "worker_lost")
    elif not same_adapter(item.golden.env.harness_version, item.replay.env.harness_version):
        # ADR 0003: never judge a replay produced by a different adapter than its golden.
        _log.warning("orchestrator.harness_mismatch", golden=item.golden.run_id)
        pair = failed_pair(item.golden, item.replay, "harness_mismatch")
    else:
        async with sem:
            pair = await _cascade(ctx, item, diff)
    await _save(ctx, path, pair)
    await emit(ctx, "pair.decided", {**_ids(pair), "outcome": pair.outcome})
    return pair


async def handle_judging(ctx: EditContext) -> str:
    items = await asyncio.to_thread(collect_replays, ctx)
    diff = await asyncio.to_thread(ctx.store.load_diff, ctx.edit_id)
    sem = asyncio.Semaphore(ctx.services.judge_concurrency)
    await asyncio.gather(*(_judge_one(ctx, item, diff, sem) for item in items))
    return "drifting"


# --- drifting ---------------------------------------------------------------------------------
def _eligible(pair: PairVerdict, golden: Trace) -> bool:
    return (
        golden.world_dependent
        and pair.outcome in ("regression", "uncertain")
        and pair.decided_by != "level0"
        and bool(pair.replay_run_id)
    )


async def _drift_one(ctx: EditContext, pair: PairVerdict, sem: asyncio.Semaphore) -> None:
    store = ctx.store
    marker = pair_path(ctx, pair.golden_run_id, ".drift")
    if marker.is_file():
        return
    golden = await asyncio.to_thread(store.load_trace, pair.golden_run_id)
    if not _eligible(pair, golden):
        return
    replay = await asyncio.to_thread(store.load_trace, pair.replay_run_id)
    async with sem:
        try:
            checked = await check_pair(pair, golden, replay, ctx.client, ctx.tavily)
        except RatchetError as exc:
            _log.warning("orchestrator.drift_failed", golden=golden.run_id, code=exc.code)
            checked = pair.model_copy(update={"error_code": pair.error_code or exc.code})
    for check in checked.drift:
        body = {"fact_id": check.fact_id, "changed": check.changed, "status": check.status}
        await emit(ctx, "pair.drift", {**_ids(checked), **body, "confidence": check.confidence})
    await _save(ctx, marker, checked)
    if checked.outcome != pair.outcome:
        await emit(ctx, "pair.decided", {**_ids(checked), "outcome": checked.outcome})


async def handle_drifting(ctx: EditContext) -> str:
    if not (ctx.settings.ratchet_feature_drift and ctx.services.cascade.drift):
        return "deciding"
    pairs = await asyncio.to_thread(load_pairs, ctx)
    sem = asyncio.Semaphore(ctx.services.judge_concurrency)
    await asyncio.gather(*(_drift_one(ctx, pair, sem) for pair in pairs))
    return "controlling"
