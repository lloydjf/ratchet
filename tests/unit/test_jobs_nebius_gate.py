"""NebiusJobRunner: stateDetails handling, the in-flight cap and the quota backoff (respx)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from ratchet.config.settings import Settings
from ratchet.errors import UpstreamError
from ratchet.models import ReplayBatch
from ratchet.replay import worker
from ratchet.replay.jobs.gate import Backoff, SubmitGate
from ratchet.replay.jobs.nebius import NebiusJobRunner, is_quota_exhausted
from ratchet.replay.jobs.nebius_spec import CONTAINER_FAILED_EXIT

API = "https://api.nebius.cloud"
S3 = "https://storage.eu-north1.nebius.cloud"
OUT = f"{S3}/ratchet-io/batches/batch_42/out"
CONTAINER_FAILED = {"code": "ContainerFailed", "message": "exit status 1"}
FOREIGN = {"metadata": {"id": "aijob-x", "labels": {}}, "status": {"state": "RUNNING"}}
QUOTA = {"code": 8, "message": "Quota limit exceeded: compute.instance.count"}


def _settings(**extra: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        nebius_api_key="iam-token-xyz",
        nebius_project_id="project-e00abc",
        nebius_s3_bucket="ratchet-io",
        nebius_s3_access_key_id="AKIDEXAMPLE",
        nebius_s3_secret_access_key="s3secret0123456789",
        **extra,
    )


def _spec(batch_id: str = "batch_42") -> ReplayBatch:
    return ReplayBatch(
        batch_id=batch_id,
        edit_id="edit_1",
        golden_run_ids=["run_a"],
        skills_tar_ref="s",
        cassettes_ref="c",
        worker_image="ghcr.io/x/ratchet-worker:sha",
        agent_tier="super",
        sandbox="docker",
        timeout_s=600,
    )


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _job(job_id: str, state: str, **status: object) -> dict[str, object]:
    labels = {"ratchet-batch-id": "batch_42"}
    return {"metadata": {"id": job_id, "labels": labels}, "status": {"state": state, **status}}


def _mock_submit_io() -> respx.Route:
    respx.get(f"{API}/ai/v1/jobs/get-by-name").respond(404)
    respx.put(url__startswith=f"{S3}/ratchet-io/batches/").respond(200)
    return respx.post(f"{API}/ai/v1/jobs")


def _inputs(tmp_path: Path) -> Path:
    (tmp_path / "spec.json").write_text("{}")
    return tmp_path


def test_container_failed_exit_is_the_workers_sandbox_code():
    assert CONTAINER_FAILED_EXIT == worker.EXIT_SANDBOX


@respx.mock
@pytest.mark.parametrize(
    ("state", "details", "exit_json", "expected"),
    [
        ("COMPLETED", None, None, ("succeeded", 0, None)),
        ("FAILED", CONTAINER_FAILED, None, ("failed", 4, "ContainerFailed")),
        ("FAILED", {"code": "TimeoutExceeded"}, None, ("failed", None, "TimeoutExceeded")),
        ("ERROR", {"code": "NotEnoughResources"}, None, ("failed", None, "NotEnoughResources")),
        ("FAILED", {"code": "ContainerFailed"}, {"exit_code": 3}, ("failed", 3, "ContainerFailed")),
        ("COMPLETED", None, {"exit_code": 5}, ("failed", 5, None)),
        ("CANCELLED", None, None, ("cancelled", None, None)),
    ],
)  # fmt: skip
async def test_poll_maps_state_details_and_prefers_the_workers_exit_file(
    state, details, exit_json, expected
):
    status = {"stateDetails": details} if details else {}
    respx.get(f"{API}/ai/v1/jobs/aijob-9").respond(200, json=_job("aijob-9", state, **status))
    respx.get(f"{OUT}/progress.json").respond(404)
    exit_route = respx.get(f"{OUT}/exit.json")
    exit_route.respond(200, json=exit_json) if exit_json else exit_route.respond(404)
    got = await NebiusJobRunner(_settings(), sleep=Sleeps()).poll("aijob-9")
    assert (got.state, got.exit_code, got.detail) == expected and got.terminal


def test_quota_errors_are_recognised():
    assert is_quota_exhausted(httpx.Response(429))
    assert is_quota_exhausted(httpx.Response(400, json={"code": "RESOURCE_EXHAUSTED"}))
    assert is_quota_exhausted(httpx.Response(403, json=QUOTA))
    assert not is_quota_exhausted(httpx.Response(400, json={"code": 3, "message": "bad args"}))
    assert not is_quota_exhausted(httpx.Response(200, json={"resourceId": "aijob-1"}))


@respx.mock
async def test_quota_exhausted_create_backs_off_then_succeeds(tmp_path: Path):
    create = _mock_submit_io()
    create.side_effect = [
        httpx.Response(429, json=QUOTA),
        httpx.Response(429, json=QUOTA),
        httpx.Response(200, json={"resourceId": "aijob-1"}),
    ]
    sleeps = Sleeps()
    runner = NebiusJobRunner(_settings(), sleep=sleeps)
    assert await runner.submit(_spec(), _inputs(tmp_path)) == "aijob-1"
    assert create.call_count == 3 and sleeps.delays == [15.0, 30.0]
    assert runner.gate.in_flight == {"aijob-1"}


@respx.mock
async def test_quota_that_stays_exhausted_fails_after_the_wait_budget(tmp_path: Path, capsys):
    create = _mock_submit_io()
    create.respond(429, json=QUOTA)
    sleeps = Sleeps()
    runner = NebiusJobRunner(_settings(ratchet_jobs_submit_wait_s=200), sleep=sleeps)
    with pytest.raises(UpstreamError) as info:
        await runner.submit(_spec(), _inputs(tmp_path))
    assert info.value.code == "upstream_unavailable"
    assert info.value.context["reason"] == "quota_exhausted"
    assert sleeps.delays == [15.0, 30.0, 60.0, 60.0, 35.0] and sum(sleeps.delays) == 200
    assert runner.gate.in_flight == frozenset()
    assert runner.gate.max_in_flight - len(runner.gate.in_flight) == 2  # reservation freed
    captured = capsys.readouterr()
    lines = (captured.out + captured.err).splitlines()
    events = [json.loads(line) for line in lines if line.startswith("{")]
    codes = {(e["event"], e.get("code")) for e in events}
    assert ("jobs.submit_waiting", "quota_exhausted") in codes
    assert ("jobs.submit_gave_up", "quota_exhausted") in codes
    assert "Quota limit" not in json.dumps(events)


@respx.mock
async def test_third_submit_waits_for_a_slot_and_refreshes_from_the_job_list(tmp_path: Path):
    create = _mock_submit_io()
    create.side_effect = [httpx.Response(200, json={"resourceId": f"aijob-{i}"}) for i in (1, 2, 3)]
    listing = respx.get(f"{API}/ai/v1/jobs")
    listing.side_effect = [
        httpx.Response(200, json={"items": [_job(f"aijob-{i}", "RUNNING") for i in (1, 2)]}),
        httpx.Response(200, json={"items": [_job("aijob-1", "COMPLETED")], "nextPageToken": "p2"}),
        httpx.Response(200, json={"items": [_job("aijob-2", "RUNNING"), FOREIGN]}),
    ]  # fmt: skip
    sleeps = Sleeps()
    runner = NebiusJobRunner(_settings(), sleep=sleeps)
    inputs = _inputs(tmp_path)
    ids = [await runner.submit(_spec(f"batch_{i}"), inputs) for i in (1, 2, 3)]

    assert ids == ["aijob-1", "aijob-2", "aijob-3"]
    assert sleeps.delays == [15.0]
    assert listing.call_count == 3
    assert dict(listing.calls[0].request.url.params) == {
        "parentId": "project-e00abc",
        "pageSize": "100",
    }
    assert listing.calls[2].request.url.params["pageToken"] == "p2"
    assert runner.gate.in_flight == {"aijob-2", "aijob-3"}


@respx.mock
async def test_slot_wait_gives_up_with_upstream_unavailable(tmp_path: Path):
    create = _mock_submit_io()
    create.respond(200, json={"resourceId": "never"})
    respx.get(f"{API}/ai/v1/jobs").respond(503)  # listing unavailable: keep the local count
    sleeps = Sleeps()
    runner = NebiusJobRunner(
        _settings(ratchet_jobs_max_concurrent=1, ratchet_jobs_submit_wait_s=45), sleep=sleeps
    )
    runner.gate.track("aijob-0")
    with pytest.raises(UpstreamError) as info:
        await runner.submit(_spec(), _inputs(tmp_path))
    assert info.value.context["reason"] == "jobs_slots_full"
    assert create.call_count == 0
    assert sum(d for d in sleeps.delays if d >= 15) == 45


@respx.mock
async def test_poll_terminal_and_cancel_release_slots():
    respx.get(f"{API}/ai/v1/jobs/aijob-1").respond(200, json=_job("aijob-1", "RUNNING"))
    respx.get(f"{API}/ai/v1/jobs/aijob-2").respond(200, json=_job("aijob-2", "COMPLETED"))
    respx.get(url__startswith=f"{OUT}/").respond(404)
    respx.post(f"{API}/ai/v1/jobs/cancel").respond(200, json={})
    runner = NebiusJobRunner(_settings(), sleep=Sleeps())
    for job_id in ("aijob-1", "aijob-2", "aijob-3"):
        runner.gate.track(job_id)
    await runner.poll("aijob-1")
    assert runner.gate.in_flight == {"aijob-1", "aijob-2", "aijob-3"}
    await runner.poll("aijob-2")
    assert runner.gate.in_flight == {"aijob-1", "aijob-3"}
    await runner.cancel("aijob-3")
    assert runner.gate.in_flight == {"aijob-1"}


async def test_backoff_is_capped_and_bounded_by_its_budget():
    sleeps = Sleeps()
    backoff = Backoff(900, sleeps)
    while await backoff.pause():
        pass
    assert sleeps.delays[:4] == [15.0, 30.0, 60.0, 60.0] and max(sleeps.delays) == 60.0
    assert sum(sleeps.delays) == 900 and backoff.waited_s == 900
    assert not await Backoff(0, sleeps).pause()


async def test_gate_without_refresh_counts_reservations():
    gate = SubmitGate(1)
    await gate.acquire(Backoff(0, Sleeps()), batch_id="b1")
    with pytest.raises(UpstreamError):
        await gate.acquire(Backoff(0, Sleeps()), batch_id="b2")
    gate.settle(None)
    await gate.acquire(Backoff(0, Sleeps()), batch_id="b3")
    gate.settle("aijob-3")
    assert gate.in_flight == {"aijob-3"} and gate.release("aijob-3")
    assert not gate.release("aijob-3")
