"""Injected failures where they cross the orchestrator: killed workers, outages, stalls.

Shares the e2e fixtures (``tests/e2e/e2e_support.py``): seeded demo, simulator upstream,
local process runner, process sandbox.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from e2e_support import (  # noqa: E402
    Demo,
    demo,
    demo_template,
    orchestrator_for,
    scenario_files,
    submit_operator_edit,
    verdict_after,
)

from ratchet.judge.tavily import FixtureTavily  # noqa: E402
from ratchet.models import ReplayBatch  # noqa: E402
from ratchet.orchestrator.context import InProcessProxy  # noqa: E402
from ratchet.orchestrator.simulate import SimulateService  # noqa: E402
from ratchet.replay.jobs import JobStatus, LocalJobRunner  # noqa: E402

pytestmark = pytest.mark.e2e
__all__ = ["demo", "demo_template"]  # fixtures re-exported for pytest


class FailingSuper(httpx.AsyncBaseTransport):
    """The simulator upstream, except every Super ``check`` call answers 503."""

    def __init__(self) -> None:
        from ratchet.sim.app import create_app

        self._inner = httpx.ASGITransport(app=create_app())
        self.refused = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("x-ratchet-prompt", "").startswith("check@"):
            self.refused += 1
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return await self._inner.handle_async_request(request)


async def test_super_503_judges_uncertain_and_still_decides(demo: Demo) -> None:
    """Super unavailable → its opinions are uncertain; a verdict is still produced
    and nothing the cascade could not confirm is blocked."""
    upstream = FailingSuper()
    proxy = InProcessProxy(demo.settings, demo.store, upstream_transport=upstream)
    orchestrator = orchestrator_for(demo, judge_http=proxy.client)
    await orchestrator.start(watch=False)
    try:
        service = SimulateService(demo.settings, demo.store, orchestrator)
        start = await service.start("2", "e" * 16)
        await orchestrator.wait_idle(120)
    finally:
        await orchestrator.stop()
        await proxy.aclose()
    verdict = demo.store.load_verdict(str(start.verdict_id))
    assert upstream.refused > 0
    supers = [o for p in verdict.pairs for o in p.opinions if o.tier == "super"]
    assert supers and all(o.verdict == "uncertain" for o in supers)
    assert any("upstream_unavailable" in o.reasons for o in supers)
    assert verdict.decision in ("WARN", "BLOCK") and verdict.narrative


async def test_tavily_unavailable_keeps_the_pairs_flagged(demo: Demo, tmp_path: Path) -> None:
    """No Tavily → drift checks ``unavailable``, ``drift_unavailable`` on the pair,
    the outcome stays as the cascade left it, and the edit is not passed."""
    empty = tmp_path / "no-tavily"
    empty.mkdir()
    orchestrator = orchestrator_for(demo, tavily_factory=lambda s, st, o: FixtureTavily(empty))
    await orchestrator.start(watch=False)
    try:
        service = SimulateService(demo.settings, demo.store, orchestrator)
        start = await service.start("3", "f" * 16)
        await orchestrator.wait_idle(120)
    finally:
        await orchestrator.stop()
    verdict = demo.store.load_verdict(str(start.verdict_id))
    world = [p for p in verdict.pairs if p.drift]
    assert len(world) == 4
    for pair in world:
        assert pair.outcome in ("regression", "uncertain")
        assert pair.error_code == "drift_unavailable"
        assert all(c.status == "unavailable" for c in pair.drift)
    assert verdict.decision == "WARN" and verdict.action == "held"


class StallingRunner:
    """A runner whose jobs never make progress (a hung Serverless Job)."""

    name = "nebius"

    def __init__(self, store: object = None) -> None:
        self.cancelled: list[str] = []
        self.store = store
        self.status_at_cancel: list[str] = []

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        return f"stuck-{spec.batch_id}"

    async def poll(self, job_id: str) -> JobStatus:
        return JobStatus("running", 0, 0, None, None)

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        return output_dir

    async def cancel(self, job_id: str) -> None:
        self.cancelled.append(job_id)
        if self.store is not None:  # the stall is persisted before the cancel
            batch_id = job_id.removeprefix("stuck-")
            row = self.store.query_one("SELECT status FROM batches WHERE batch_id = ?", (batch_id,))
            self.status_at_cancel.append(str(row["status"]))


async def test_stalled_batches_are_retried_on_the_local_runner(demo: Demo) -> None:
    """No heartbeat for ``stall_s`` → persisted ``stalled`` + ``batch.stalled``, then
    cancel, retry once locally (``retried``), same verdict."""
    from ratchet.orchestrator import prom

    prom.reset()
    stuck = StallingRunner(demo.store)
    orchestrator = orchestrator_for(demo, runner=stuck, stall_s=3.0)
    await orchestrator.start(watch=False)
    try:
        edit = submit_operator_edit(demo, orchestrator, scenario_files("1"))
        verdict = await verdict_after(demo, orchestrator, edit)
    finally:
        await orchestrator.stop()
    assert len(stuck.cancelled) == len(verdict.batch_ids) >= 1
    assert stuck.status_at_cancel == ["stalled"] * len(stuck.cancelled)
    assert verdict.runner == "local-retry"
    assert verdict.decision == "BLOCK"
    stalls = demo.store.events(kinds=["batch.stalled"], limit=100)
    assert {e.payload["batch_id"] for e in stalls} == set(verdict.batch_ids)
    assert all(e.payload["status"] == "stalled" and e.payload["runner"] == "nebius" for e in stalls)
    retried = demo.store.events(kinds=["batch.submitted"], limit=100)
    assert sum(e.payload["status"] == "retried" for e in retried) == len(verdict.batch_ids)
    rows = demo.store.batches_for_edit(edit.edit_id)
    assert {r.status for r in rows} <= {"ok", "partial"}  # retried batches finished
    reg = prom.metrics().registry
    stalled = reg.get_sample_value("ratchet_jobs_total", {"runner": "nebius", "status": "stalled"})
    assert stalled == len(verdict.batch_ids)
    prom.reset()


class KillingRunner(LocalJobRunner):
    """A local runner that kills each worker after its first replay."""

    async def poll(self, job_id: str) -> JobStatus:
        status = await super().poll(job_id)
        if status.k >= 1 and not status.terminal:
            await self.cancel(job_id)
        return status


async def test_killed_workers_hold_the_edit_for_replay_infra(demo: Demo) -> None:
    """Killed workers through the orchestrator: partial batches → one retry round for the lost
    replays (killed again) → failed pairs → WARN replay_infra."""
    runner = KillingRunner("process", settings=demo.settings, work_root=demo.root / "kjobs")
    orchestrator = orchestrator_for(demo, runner=runner)
    await orchestrator.start(watch=False)
    try:
        edit = submit_operator_edit(demo, orchestrator, scenario_files("3"))
        verdict = await verdict_after(demo, orchestrator, edit)
    finally:
        await orchestrator.stop()
    failed = [p for p in verdict.pairs if p.outcome == "failed"]
    assert failed and all(p.decided_by == "infra" for p in failed)
    assert verdict.decision == "WARN" and verdict.reasons[0] == "replay_infra"
    retries = [b for b in verdict.batch_ids if b.startswith("retry_")]
    assert retries  # the lost replays were dispatched once more, and only once
    submitted = demo.store.events(ref=edit.edit_id, kinds=["batch.submitted"], limit=100)
    assert sorted(e.payload["batch_id"] for e in submitted if e.payload["status"] == "retry") == (
        sorted(retries)
    )
