"""The stall rule: a running worker must heartbeat within ``stall_s``; a job the platform
still reports as pending (provisioning, pulling the image) gets ``provision_s`` instead."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from ratchet import clock
from ratchet.orchestrator.replaying import _stalled
from ratchet.replay.jobs import JobStatus
from ratchet.store.records import BatchRow

T0 = clock.parse("2026-10-16T21:00:00+00:00")


def _row(last_beat_s_ago: float, k: int = 0) -> BatchRow:
    return BatchRow(
        batch_id="batch_x_a1_00",
        edit_id="edit_x",
        status="running",
        runner="nebius",
        job_id="aijob-1",
        attempts=1,
        progress_k=k,
        progress_n=4,
        last_heartbeat_at=T0 - timedelta(seconds=last_beat_s_ago),
    )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(services=SimpleNamespace(stall_s=120.0, provision_s=900.0))


def test_a_pending_job_gets_the_provisioning_budget() -> None:
    pending = JobStatus("pending", 0, 0, None, None)
    with clock.frozen(T0):
        assert not _stalled(_ctx(), _row(300), pending)  # a 5-minute cold start is normal
        assert _stalled(_ctx(), _row(901), pending)  # past provision_s: stalled


def test_a_running_job_keeps_the_heartbeat_budget() -> None:
    running = JobStatus("running", 0, 4, None, None)
    with clock.frozen(T0):
        assert not _stalled(_ctx(), _row(119), running)
        assert _stalled(_ctx(), _row(121), running)
        # pending by state but already past its first replay: the worker is alive, so 120 s
        assert _stalled(_ctx(), _row(121, k=1), JobStatus("pending", 1, 4, None, None))


def test_an_unpolled_batch_uses_the_heartbeat_budget() -> None:
    with clock.frozen(T0):
        assert _stalled(_ctx(), _row(121), None)
        assert not _stalled(_ctx(), _row(60), None)
        row = _row(0)
        row = BatchRow(**{**row.__dict__, "last_heartbeat_at": None})
        assert not _stalled(_ctx(), row, None)
