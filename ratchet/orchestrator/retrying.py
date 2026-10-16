"""collecting → judging, with one automatic retry round for crashed replays (live E6).

A single transient crash (``harness_failed``, ``sandbox_failed``, ``worker_lost``) used to
make its pair ``failed`` and the whole edit WARN ``replay_failed``, though every other pair
passed. So after the edit's own batches finish, the goldens whose replay crashed are
re-dispatched **once**:

- **ids**: ``retry_<hex>_a<n>_<ii>`` (:func:`ratchet.orchestrator.ids.retry_batch_id_for`),
  planned like the edit's replays (same candidate tree, tier/harness grouping, world
  overlay); normally one batch per edit. They are saved like any batch, and a batch with a
  job id is never resubmitted; the goldens are always computed from the edit's own
  ``batch_`` results, never from a retry's, so a restart never retries twice.
- **budget**: the batches carry ``budget_allowance_usd`` (what is left now); when the
  daily or the ``agent`` cap is already spent, nothing is retried.
- **events**: ``batch.submitted`` with ``status: "retry"``; the progress strip shows
  "retrying 1 failed replay" on the replaying stage (``copy.retry_detail``).
- **judging** (``judging.collect_replays``) takes a retried golden's result from the retry
  batch in place of the failure; a retry that fails again leaves the pair ``failed``.

``RATCHET_REPLAY_RETRY_ONCE=false`` turns the round off. ``budget_exceeded`` is never
retried: the cap stopped it, a retry would only be refused again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ratchet.errors import RatchetError
from ratchet.log import get_logger
from ratchet.models import ReplayBatch
from ratchet.orchestrator.context import EditContext
from ratchet.orchestrator.ids import retry_batch_id_for
from ratchet.orchestrator.replaying import (
    collect_batches,
    current_batches,
    plan_batches,
    sandbox_refusal,
    submit_prepared,
)
from ratchet.replay.packing import pack_batch_inputs
from ratchet.store import BatchRow

__all__ = [
    "RETRY_CODES",
    "retry_batches",
    "retry_candidates",
    "allowance_spent",
    "handle_collecting",
]

RETRY_CODES: frozenset[str] = frozenset({"harness_failed", "sandbox_failed", "worker_lost"})
_CAPS = ("daily", "agent")  # the caps an agent replay's spend counts against
_log = get_logger("orchestrator")


def retry_batches(ctx: EditContext) -> list[BatchRow]:
    prefix = retry_batch_id_for(ctx.edit_id, ctx.options.attempt, 0)[:-2]
    return [b for b in ctx.store.batches_for_edit(ctx.edit_id) if b.batch_id.startswith(prefix)]


def retry_candidates(ctx: EditContext) -> list[str]:
    """Selected goldens whose replay in the edit's own batches crashed, in selection order."""
    store = ctx.store
    failed: dict[str, str] = {}
    for row in current_batches(ctx):
        result = store.load_batch_result(row.batch_id)
        if result is None:
            spec = store.load_batch_spec(row.batch_id)
            failed.update(dict.fromkeys(spec.golden_run_ids, "worker_lost"))
            continue
        failed.update(result.failed_goldens)
    selected = [sel.run_id for sel in store.load_selection(ctx.edit_id)]
    return [run_id for run_id in selected if failed.get(run_id) in RETRY_CODES]


def allowance_spent(allowance: dict[str, float] | None) -> bool:
    """True when a cap a replay counts against has nothing left (``None`` = no caps)."""
    return allowance is not None and any(allowance.get(cap, 1.0) <= 0.0 for cap in _CAPS)


def _prepare(ctx: EditContext, run_ids: list[str]) -> list[tuple[ReplayBatch, Path]]:
    ctx.work.mkdir(parents=True, exist_ok=True)
    tar = ctx.work / "skills.tar.zst"
    if not tar.is_file():
        ctx.repo.checkout_tar(ctx.edit.commit, tar)
    attempt = ctx.options.attempt
    specs = plan_batches(
        ctx,
        run_ids,
        tar,
        commit=ctx.edit.commit,
        batch_id=lambda i: retry_batch_id_for(ctx.edit_id, attempt, i),
    )
    known = {b.batch_id for b in retry_batches(ctx) if b.job_id}
    todo = [spec for spec in specs if spec.batch_id not in known]
    if any(allowance_spent(spec.budget_allowance_usd) for spec in todo):
        _log.warning("orchestrator.retry_skipped", edit_id=ctx.edit_id, code="budget_exceeded")
        return []
    prepared: list[tuple[ReplayBatch, Path]] = []
    for spec in todo:
        inputs = ctx.store.batch_dir(spec.batch_id) / "inputs"
        pack_batch_inputs(ctx.store, spec, inputs, skills_src=tar)
        prepared.append((spec, inputs))
    return prepared


async def _retry_failed(ctx: EditContext) -> None:
    """Dispatch (once) and collect the retry round; any failure leaves the pairs failed."""
    run_ids = await asyncio.to_thread(retry_candidates, ctx)
    if not run_ids:
        return
    try:
        prepared = await asyncio.to_thread(_prepare, ctx, run_ids)
    except RatchetError as exc:
        _log.warning("orchestrator.retry_failed", edit_id=ctx.edit_id, code=exc.code)
        return
    if any(sandbox_refusal(ctx.settings, spec.harness) for spec, _ in prepared):
        _log.warning("orchestrator.retry_refused", edit_id=ctx.edit_id)
        return
    if prepared:
        _log.info("orchestrator.retrying", edit_id=ctx.edit_id, n=len(run_ids))
        key, runner = ctx.services.pick(ctx.options.runner)
        if not await submit_prepared(ctx, key, runner, prepared, status="retry"):
            _log.warning("orchestrator.retry_failed", edit_id=ctx.edit_id, code="submit_failed")
    await collect_batches(ctx, _submitted)


def _submitted(ctx: EditContext) -> list[BatchRow]:
    return [b for b in retry_batches(ctx) if b.job_id]


async def handle_collecting(ctx: EditContext) -> str:
    await collect_batches(ctx, current_batches)
    if ctx.settings.ratchet_replay_retry_once:
        await _retry_failed(ctx)
    return "judging"
