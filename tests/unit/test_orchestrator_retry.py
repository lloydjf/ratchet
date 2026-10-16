"""One automatic retry for crashed replays (collecting → judging): dispatched once, never
twice after a restart, off by setting, refused when the budget is spent, judged in place
of the failure, and shown on the progress strip."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import (  # noqa: E402
    T0,
    commit_edit,
    fixture_trace,
    make_settings,
    open_store,
    seeded_repo,
)

from ratchet import clock, copy  # noqa: E402
from ratchet.models import ReplayBatch, ReplayResult, Trace  # noqa: E402
from ratchet.orchestrator.context import EditContext, Services, build_context  # noqa: E402
from ratchet.orchestrator.embed import LocalHashEmbedder  # noqa: E402
from ratchet.orchestrator.judging import collect_replays  # noqa: E402
from ratchet.orchestrator.progress import progress_for  # noqa: E402
from ratchet.orchestrator.replaying import handle_dispatching  # noqa: E402
from ratchet.orchestrator.retrying import (  # noqa: E402
    allowance_spent,
    handle_collecting,
    retry_batches,
)
from ratchet.replay.jobs.base import JobStatus  # noqa: E402
from ratchet.store import SelectedTrace  # noqa: E402
from ratchet.store.base import write_json_atomic  # noqa: E402

FIXTURE = "r2_dropped_field"


class CrashingRunner:
    """Finishes every batch at once. ``crash[prefix]`` maps a golden to the failure code its
    replay gets in batches whose id starts with ``prefix``; every other golden replays as a
    copy of itself."""

    name = "fake"

    def __init__(self, store: Any, crash: dict[str, dict[str, str]]) -> None:
        self.store = store
        self.crash = crash
        self.submits: list[ReplayBatch] = []

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        self.submits.append(spec)
        return f"job-{spec.batch_id}"

    async def poll(self, job_id: str) -> JobStatus:
        return JobStatus("succeeded", 1, 1, clock.now(), 0)

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        spec = next(s for s in reversed(self.submits) if job_id == f"job-{s.batch_id}")
        crash = next((c for p, c in self.crash.items() if spec.batch_id.startswith(p)), {})
        replays: list[Trace] = []
        failed: dict[str, str] = {}
        for run_id in spec.golden_run_ids:
            if run_id in crash:
                failed[run_id] = crash[run_id]
                continue
            golden: Trace = self.store.load_trace(run_id)
            update = {"run_id": f"rep_{spec.batch_id}_{run_id}", "kind": "replay"}
            replays.append(golden.model_copy(update={**update, "golden_of": run_id}))
        result = ReplayResult(
            batch_id=spec.batch_id,
            replays=replays,
            unexpected_side_effects=[],
            worker_log_ref="",
            status="partial" if failed else "ok",
            failed_goldens=failed,
        )
        write_json_atomic(output_dir / "result.json", result)
        return output_dir

    async def cancel(self, job_id: str) -> None:
        return None


def _setup(
    tmp_path: Path, crash: dict[str, dict[str, str]], **over: Any
) -> tuple[EditContext, CrashingRunner, list[str]]:
    settings = make_settings(tmp_path, ratchet_sandbox="docker", **over)
    store = open_store(settings)
    runner = CrashingRunner(store, crash)
    services = Services(
        settings=settings,
        store=store,
        runner=runner,  # type: ignore[arg-type]
        retry=runner,  # type: ignore[arg-type]
        judge_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        embedder=LocalHashEmbedder(),
        poll_s=0.0,
    )
    repo = seeded_repo(settings)
    edit, diff = commit_edit(repo, {"expense-report/NOTE.md": "x\n"})
    store.save_edit(edit, diff)
    golden = fixture_trace(FIXTURE, "golden")
    other = golden.model_copy(update={"run_id": f"{golden.run_id}b"})
    ids = [golden.run_id, other.run_id]
    for trace in (golden, other):
        store.save_trace(trace)
    store.save_selection(edit.edit_id, [SelectedTrace(i, "direct", 1.0) for i in ids])
    return build_context(services, edit.edit_id), runner, ids


def _submitted(ctx: EditContext) -> list[dict[str, object]]:
    return [e.payload for e in ctx.store.events(ref=ctx.edit_id, kinds=["batch.submitted"])]


@pytest.mark.parametrize("code", ["harness_failed", "sandbox_failed", "worker_lost"])
async def test_a_crashed_replay_is_dispatched_again_once_and_judged_from_the_retry(
    tmp_path: Path, code: str
) -> None:
    ctx, runner, (ok, crashed) = _setup(tmp_path, {})
    runner.crash = {"batch_": {crashed: code}}
    assert await handle_dispatching(ctx) == "collecting"
    assert await handle_collecting(ctx) == "judging"
    first, retry = runner.submits
    assert first.batch_id.startswith("batch_") and retry.golden_run_ids == [crashed]
    assert retry.batch_id.startswith("retry_") and retry.batch_id.endswith("_a1_00")
    assert retry.candidate_commit == ctx.edit.commit
    events = _submitted(ctx)
    assert [e["status"] for e in events] == ["submitted", "retry"]
    assert events[-1]["batch_id"] == retry.batch_id and events[-1]["n"] == 1
    by_golden = {item.golden.run_id: item for item in collect_replays(ctx)}
    assert by_golden[crashed].replay is not None and by_golden[crashed].error_code is None
    assert by_golden[crashed].replay.run_id.startswith("rep_retry_")
    assert by_golden[ok].replay is not None


async def test_a_restart_never_retries_twice(tmp_path: Path) -> None:
    ctx, runner, (_ok, crashed) = _setup(tmp_path, {})
    runner.crash = {"batch_": {crashed: "harness_failed"}}
    await handle_dispatching(ctx)
    await handle_collecting(ctx)
    await handle_collecting(ctx)  # restarted in collecting: the round is already on record
    assert len(runner.submits) == 2 and len(retry_batches(ctx)) == 1
    assert [e["status"] for e in _submitted(ctx)] == ["submitted", "retry"]


async def test_a_retry_that_crashes_again_leaves_the_pair_failed(tmp_path: Path) -> None:
    ctx, runner, (_ok, crashed) = _setup(tmp_path, {})
    runner.crash = {"batch_": {crashed: "harness_failed"}, "retry_": {crashed: "sandbox_failed"}}
    await handle_dispatching(ctx)
    await handle_collecting(ctx)
    await handle_collecting(ctx)
    assert len(runner.submits) == 2  # a failed retry is not retried
    item = next(i for i in collect_replays(ctx) if i.golden.run_id == crashed)
    assert item.replay is None and item.error_code == "sandbox_failed"


@pytest.mark.parametrize(
    ("settings", "code"),
    [
        ({"ratchet_replay_retry_once": False}, "harness_failed"),  # feature off
        ({"ratchet_daily_usd_cap": 0.0}, "harness_failed"),  # nothing left to spend
        ({}, "budget_exceeded"),  # the cap stopped it: a retry would be refused again
        ({}, "harness_mismatch"),  # not a crash
    ],
)
async def test_no_retry_when_off_out_of_budget_or_not_a_crash(
    tmp_path: Path, settings: dict[str, object], code: str
) -> None:
    ctx, runner, (_ok, crashed) = _setup(tmp_path, {}, **settings)
    runner.crash = {"batch_": {crashed: code}}
    await handle_dispatching(ctx)
    assert await handle_collecting(ctx) == "judging"
    assert len(runner.submits) == 1 and retry_batches(ctx) == []
    item = next(i for i in collect_replays(ctx) if i.golden.run_id == crashed)
    assert item.replay is None and item.error_code == code


def test_allowance_spent_reads_the_caps_a_replay_counts_against() -> None:
    assert not allowance_spent(None)
    assert not allowance_spent({"daily": 1.0, "bench": 0.0})  # bench is not the agent's
    assert allowance_spent({"daily": 0.0})
    assert allowance_spent({"daily": 2.0, "agent": 0.0})


def test_the_replaying_stage_shows_the_retry(tmp_path: Path) -> None:
    ctx, _runner, _ids = _setup(tmp_path, {})
    store, edit_id = ctx.store, ctx.edit_id

    def emit(kind: str, at_s: float, **payload: object) -> None:
        with clock.frozen(T0 + timedelta(seconds=at_s)):
            store.emit(kind, edit_id, {"edit_id": edit_id, "runner": "local-process", **payload})

    emit("batch.finished", 1, batch_id="batch_x_a1_00", k=2, n=2, status="partial")
    emit("batch.submitted", 2, batch_id="retry_x_a1_00", k=0, n=1, status="retry")
    with clock.frozen(T0 + timedelta(seconds=3)):
        store.set_edit_state(edit_id, "collecting")
        progress = progress_for(store, edit_id)
    assert progress is not None and (progress.k, progress.n) == (2, 2)  # the edit's replays
    details = {s.key: s.detail for s in progress.stages}
    assert details["replaying"] == "retrying 1 failed replay"
    emit("batch.finished", 4, batch_id="retry_x_a1_00", k=1, n=1, status="ok")
    with clock.frozen(T0 + timedelta(seconds=5)):
        progress = progress_for(store, edit_id)
    assert progress is not None
    assert {s.key: s.detail for s in progress.stages}["replaying"] == "retried 1 failed replay"
    assert copy.retry_detail(2) == "retrying 2 failed replays"
