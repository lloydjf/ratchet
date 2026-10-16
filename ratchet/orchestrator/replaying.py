"""selecting → dispatching → collecting (design §3).

- **selecting**: ``select_traces`` (cap, neighborhood threshold, the demo's canaries); the
  selection is persisted; 0 traces → ``deciding`` (PASS ``no_affected_traces``).
- **dispatching**: goldens are grouped by (agent tier, harness) so a replay never runs on a
  different tier or adapter than its golden (ADR 0003, ``harness_mismatch``), batched by
  ``ratchet_batch_size``; the candidate checkout is tarred once per edit; each batch is packed
  and submitted (idempotent: a batch with a job id is never resubmitted; batch ids are
  deterministic per edit and attempt). A submit that fails after retries → ``failed_infra``.
- **collecting**: every ``poll_s`` each running batch is polled; ``batch.heartbeat`` when
  ``k/n`` moves; a batch with no heartbeat for ``stall_s`` (120 s) is persisted as
  ``stalled`` (+ a ``batch.stalled`` event), cancelled and retried once on the local
  runner (status ``retried`` until it finishes); a terminal batch is fetched and imported
  (``import_batch_output``). A batch that is lost twice is imported as ``failed``. The
  ``collecting`` handler itself, with its one retry round for crashed replays, lives in
  :mod:`ratchet.orchestrator.retrying`.
- Every outcome feeds the process's Prometheus registry (``orchestrator.prom``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.errors import RatchetError
from ratchet.log import get_logger
from ratchet.models import ReplayBatch, Tier
from ratchet.orchestrator import prom
from ratchet.orchestrator.context import RETRY_RUNNER, EditContext, load_canaries
from ratchet.orchestrator.ids import batch_id_for, edit_hex
from ratchet.orchestrator.journal import emit
from ratchet.orchestrator.selection import make_batches, select_traces, selection_counts
from ratchet.proxy.policy import remaining_allowance, split_allowance
from ratchet.replay.jobs import JobRunner, JobStatus
from ratchet.replay.packing import import_batch_output, pack_batch_inputs
from ratchet.store import BatchRow

__all__ = [
    "handle_selecting",
    "handle_dispatching",
    "sandbox_refusal",
    "current_batches",
    "plan_batches",
    "submit_prepared",
    "collect_batches",
    "FINISHED",
]

FINISHED: frozenset[str] = frozenset({"ok", "partial", "failed"})
_HARNESSES = ("minimal", "hermes")
_MAX_ATTEMPTS = 2
_log = get_logger("orchestrator")


# --- selecting --------------------------------------------------------------------------------
async def handle_selecting(ctx: EditContext) -> str:
    settings, store = ctx.settings, ctx.store
    canaries = await asyncio.to_thread(load_canaries, store)
    selected = await select_traces(
        store,
        ctx.repo,
        ctx.edit,
        ctx.services.embedder,
        cap=settings.ratchet_max_traces_per_edit,
        threshold=settings.ratchet_neighborhood_threshold,
        canaries=canaries,
    )
    await asyncio.to_thread(store.save_selection, ctx.edit_id, selected)
    await emit(ctx, "edit.selected", dict(selection_counts(selected)))
    return "dispatching" if selected else "deciding"


# --- dispatching ------------------------------------------------------------------------------
def current_batches(ctx: EditContext) -> list[BatchRow]:
    prefix = f"batch_{edit_hex(ctx.edit_id)}_a{ctx.options.attempt}_"
    return [b for b in ctx.store.batches_for_edit(ctx.edit_id) if b.batch_id.startswith(prefix)]


def _harness(version: str, default: str) -> str:
    name = version.split("/", 1)[0]
    return name if name in _HARNESSES else default


def _batch_size(ctx: EditContext) -> int:
    """Simulate runs use smaller batches so replays fan out across parallel workers and the
    judge path stays under 60 s; operator edits keep the default of 10 per batch."""
    if ctx.edit.session_hash is not None:
        return max(1, ctx.settings.ratchet_simulate_batch_size)
    return max(1, ctx.settings.ratchet_batch_size)


def plan_batches(
    ctx: EditContext,
    run_ids: list[str],
    skills_tar: Path,
    *,
    commit: str,
    batch_id: Callable[[int], str],
) -> list[ReplayBatch]:
    """Replay batches for ``run_ids`` against the tree ``skills_tar`` (stamped ``commit``):
    grouped by (agent tier, harness), ids from ``batch_id(index)``, caps split equally."""
    settings, store = ctx.settings, ctx.store
    groups: dict[tuple[Tier, str], list[str]] = {}
    for run_id in run_ids:
        golden = store.load_trace(run_id)
        key = (golden.agent_tier, _harness(golden.env.harness_version, settings.ratchet_harness))
        groups.setdefault(key, []).append(run_id)
    specs: list[ReplayBatch] = []
    for (tier, harness), ids in groups.items():
        for chunk in make_batches(ids, size=_batch_size(ctx)):
            specs.append(
                ReplayBatch(
                    batch_id=batch_id(len(specs)),
                    edit_id=ctx.edit_id,
                    golden_run_ids=chunk,
                    skills_tar_ref=str(skills_tar),
                    cassettes_ref="cassettes.tar.zst",
                    worker_image=settings.ratchet_worker_image,
                    agent_tier=tier,
                    sandbox=settings.ratchet_sandbox,
                    timeout_s=settings.ratchet_replay_timeout_s,
                    candidate_commit=commit,
                    harness="hermes" if harness == "hermes" else "minimal",
                    world_ref=ctx.options.world_ref,
                )
            )
    # The worker's sidecar proxy only sees its scratch ledger, so the caps travel with the
    # batch: what is left now, shared equally by the batches that run concurrently.
    allowance = split_allowance(remaining_allowance(store, settings), len(specs))
    return [spec.model_copy(update={"budget_allowance_usd": allowance}) for spec in specs]


def _plan(ctx: EditContext, skills_tar: Path) -> list[ReplayBatch]:
    ids = [sel.run_id for sel in ctx.store.load_selection(ctx.edit_id)]
    attempt = ctx.options.attempt
    return plan_batches(
        ctx,
        ids,
        skills_tar,
        commit=ctx.edit.commit,
        batch_id=lambda i: batch_id_for(ctx.edit_id, attempt, i),
    )


def _prepare(ctx: EditContext) -> list[tuple[ReplayBatch, Path]]:
    ctx.work.mkdir(parents=True, exist_ok=True)
    skills_tar = ctx.work / "skills.tar.zst"
    if not skills_tar.is_file():
        ctx.repo.checkout_tar(ctx.edit.commit, skills_tar)
    known = {b.batch_id for b in current_batches(ctx) if b.job_id}
    prepared: list[tuple[ReplayBatch, Path]] = []
    for spec in _plan(ctx, skills_tar):
        if spec.batch_id in known:
            continue
        inputs = ctx.store.batch_dir(spec.batch_id) / "inputs"
        pack_batch_inputs(ctx.store, spec, inputs, skills_src=skills_tar)
        prepared.append((spec, inputs))
    return prepared


async def _submit(ctx: EditContext, runner: JobRunner, spec: ReplayBatch, inputs: Path) -> str:
    delays = ctx.services.submit_backoff_s
    for attempt in range(len(delays) + 1):
        try:
            return await runner.submit(spec, inputs)
        except (RatchetError, OSError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            _log.warning("orchestrator.submit_failed", batch_id=spec.batch_id, code=code)
            if attempt == len(delays):
                raise
            await asyncio.sleep(delays[attempt])
    raise AssertionError("unreachable")


def sandbox_refusal(settings: Settings, harness: str | None = None) -> str | None:
    """Why no replay can run with these settings (ADR 0017), or None. ``harness`` is a
    batch's adapter (default ``RATCHET_HARNESS``): Hermes has real built-in tools and only
    runs in a sandbox that enforces egress and isolation."""
    if settings.ratchet_public_mode and settings.ratchet_sandbox == "process":
        return "RATCHET_SANDBOX=process is refused in public mode; use docker or openshell"
    if (harness or settings.ratchet_harness) == "hermes" and settings.ratchet_sandbox == "process":
        return "RATCHET_HARNESS=hermes needs RATCHET_SANDBOX=docker or openshell, not process"
    return None


async def handle_dispatching(ctx: EditContext) -> str:
    store = ctx.store
    refusal = sandbox_refusal(ctx.settings)
    if refusal is not None:
        _log.error("orchestrator.sandbox_refused", edit_id=ctx.edit_id, sandbox="process")
        await asyncio.to_thread(
            store.set_edit_state, ctx.edit_id, "dispatching", error_code="sandbox_failed"
        )
        return "failed_infra"
    key, runner = ctx.services.pick(ctx.options.runner)
    prepared = await asyncio.to_thread(_prepare, ctx)
    if any(sandbox_refusal(ctx.settings, spec.harness) for spec, _ in prepared):
        _log.error("orchestrator.sandbox_refused", edit_id=ctx.edit_id, sandbox="process")
        await asyncio.to_thread(
            store.set_edit_state, ctx.edit_id, "dispatching", error_code="sandbox_failed"
        )
        return "failed_infra"
    return "collecting" if await submit_prepared(ctx, key, runner, prepared) else "failed_infra"


async def submit_prepared(
    ctx: EditContext,
    key: str,
    runner: JobRunner,
    prepared: list[tuple[ReplayBatch, Path]],
    *,
    status: str = "submitted",
) -> bool:
    """Save, submit and record each packed batch; False when a submit fails after retries.
    ``status`` goes on the ``batch.submitted`` event (``retry`` for the crashed-replay round)."""
    store = ctx.store
    for spec, inputs in prepared:
        await asyncio.to_thread(store.save_batch_spec, spec, key)
        try:
            job_id = await _submit(ctx, runner, spec, inputs)
        except (RatchetError, OSError):
            prom.job_finished(spec.batch_id, key, "submit_failed")
            return False
        await asyncio.to_thread(
            store.update_batch,
            spec.batch_id,
            status="running",
            runner=key,
            job_id=job_id,
            attempts=1,
            submitted_at=clock.now(),
            last_heartbeat_at=clock.now(),
        )
        prom.job_submitted(spec.batch_id, key)
        n = len(spec.golden_run_ids)
        payload = {"batch_id": spec.batch_id, "runner": key, "k": 0, "n": n, "status": status}
        await emit(ctx, "batch.submitted", payload)
    return True


# --- collecting -------------------------------------------------------------------------------
async def _retry(ctx: EditContext, row: BatchRow) -> None:
    store = ctx.store
    spec = await asyncio.to_thread(store.load_batch_spec, row.batch_id)
    inputs = store.batch_dir(row.batch_id) / "inputs"
    job_id = await ctx.services.retry.submit(spec, inputs)
    prom.job_submitted(row.batch_id, RETRY_RUNNER)
    await asyncio.to_thread(
        store.update_batch,
        row.batch_id,
        status="retried",
        runner=RETRY_RUNNER,
        job_id=job_id,
        attempts=row.attempts + 1,
        last_heartbeat_at=clock.now(),
        progress_k=0,
    )
    payload = {"batch_id": row.batch_id, "runner": RETRY_RUNNER, "k": 0, "n": row.progress_n}
    await emit(ctx, "batch.submitted", {**payload, "status": "retried"})


async def _finish(ctx: EditContext, row: BatchRow, runner: JobRunner) -> None:
    store = ctx.store
    out = store.batch_dir(row.batch_id) / "output"
    try:
        await runner.fetch(str(row.job_id), out)
    except RatchetError as exc:
        _log.warning("orchestrator.fetch_failed", batch_id=row.batch_id, code=exc.code)
    lost = not (out / "result.json").is_file()
    if lost and row.attempts < _MAX_ATTEMPTS:
        prom.job_finished(row.batch_id, row.runner, "lost")
        await _retry(ctx, row)
        return
    out.mkdir(parents=True, exist_ok=True)
    result = await asyncio.to_thread(
        import_batch_output, store, out, batch_id=row.batch_id, session_hash=ctx.session_hash
    )
    k = len(result.replays)
    n = max(row.progress_n, k)
    await asyncio.to_thread(
        store.update_batch,
        row.batch_id,
        status=result.status,
        finished_at=clock.now(),
        progress_k=n,
        progress_n=n,
    )
    prom.job_finished(row.batch_id, row.runner, result.status)
    payload = {"batch_id": row.batch_id, "runner": row.runner, "k": n, "n": n}
    await emit(ctx, "batch.finished", {**payload, "status": result.status})


async def _progress(ctx: EditContext, row: BatchRow, status: JobStatus) -> None:
    beat = status.heartbeat_at
    if beat is not None:
        prom.job_heartbeat(row.batch_id)  # first beat of a submission = cold start
    moved = (status.k, status.n or row.progress_n) != (row.progress_k, row.progress_n)
    fresher = beat is not None and (row.last_heartbeat_at is None or beat > row.last_heartbeat_at)
    if not (moved or fresher):
        return
    n = status.n or row.progress_n
    await asyncio.to_thread(
        ctx.store.update_batch,
        row.batch_id,
        progress_k=status.k,
        progress_n=n,
        last_heartbeat_at=clock.now(),
    )
    if moved:
        payload = {"batch_id": row.batch_id, "runner": row.runner, "k": status.k, "n": n}
        await emit(ctx, "batch.heartbeat", {**payload, "status": "running"})


def _stalled(ctx: EditContext, row: BatchRow) -> bool:
    last = row.last_heartbeat_at
    return last is not None and (clock.now() - last).total_seconds() > ctx.services.stall_s


async def _mark_stalled(ctx: EditContext, row: BatchRow) -> None:
    """Persist ``stalled`` (the alert and the UI read it) before the retry."""
    await asyncio.to_thread(ctx.store.update_batch, row.batch_id, status="stalled")
    prom.job_finished(row.batch_id, row.runner, "stalled")
    last = row.last_heartbeat_at
    silent_s = int((clock.now() - last).total_seconds()) if last is not None else None
    payload: dict[str, object] = {
        "batch_id": row.batch_id,
        "runner": row.runner,
        "k": row.progress_k,
        "n": row.progress_n,
        "status": "stalled",
        "silent_s": silent_s,
        "attempts": row.attempts,
    }
    await emit(ctx, "batch.stalled", payload)


async def _poll_one(ctx: EditContext, row: BatchRow) -> None:
    runner = ctx.services.runner_for(row.runner)
    try:
        status = await runner.poll(str(row.job_id))
    except RatchetError as exc:
        _log.warning("orchestrator.poll_failed", batch_id=row.batch_id, code=exc.code)
        status = None
    if status is not None:
        await _progress(ctx, row, status)
        if status.terminal:
            await _finish(ctx, row, runner)
            return
    if _stalled(ctx, row):
        _log.warning("orchestrator.batch_stalled", batch_id=row.batch_id, attempts=row.attempts)
        await _mark_stalled(ctx, row)
        await runner.cancel(str(row.job_id))
        if row.attempts < _MAX_ATTEMPTS:
            await _retry(ctx, row)
        else:
            await _finish(ctx, row, runner)


async def collect_batches(
    ctx: EditContext, rows_for: Callable[[EditContext], list[BatchRow]]
) -> None:
    """Poll the batches ``rows_for`` names until every one is finished (stall → retry)."""
    while True:
        rows = await asyncio.to_thread(rows_for, ctx)
        pending = [r for r in rows if r.status not in FINISHED]
        if not pending:
            return
        for row in pending:
            await _poll_one(ctx, row)
        rows = await asyncio.to_thread(rows_for, ctx)
        if all(r.status in FINISHED for r in rows):
            return
        await asyncio.sleep(ctx.services.poll_s)
