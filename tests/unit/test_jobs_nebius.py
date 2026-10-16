"""NebiusJobRunner request construction (respx), retries, state mapping, S3 SigV4."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest
import respx

from ratchet.config.settings import Settings
from ratchet.errors import UpstreamError
from ratchet.models import ReplayBatch
from ratchet.replay.jobs.nebius import NebiusJobRunner, build_job_request, map_state
from ratchet.replay.jobs.s3 import S3Client, sigv4_headers

API = "https://api.nebius.cloud"
S3 = "https://storage.eu-north1.nebius.cloud"


def _settings(**extra: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        nebius_api_key="iam-token-xyz",
        nebius_project_id="project-e00abc",
        nebius_s3_bucket="ratchet-io",
        nebius_s3_access_key_id="AKIDEXAMPLE",
        nebius_s3_secret_access_key="s3secret0123456789",
        nebius_token_factory_key="tf-key-0123456789",
        **extra,
    )


def _spec() -> ReplayBatch:
    return ReplayBatch(
        batch_id="batch_42",
        edit_id="edit_1",
        golden_run_ids=["run_a", "run_b"],
        skills_tar_ref="s",
        cassettes_ref="c",
        worker_image="ghcr.io/x/ratchet-worker:sha",
        agent_tier="super",
        sandbox="docker",
        timeout_s=600,
    )


async def _no_sleep(_: float) -> None:
    return None


def test_job_request_has_parent_image_volumes_and_injected_spec():
    spec = _spec()
    body = build_job_request(_settings(), spec, prefix="batches/batch_42/", spec_json=b'{"a":1}')
    assert body["metadata"]["parentId"] == "project-e00abc"
    assert body["metadata"]["name"] == "ratchet-batch-42"
    job = body["spec"]
    assert job["image"] == "ghcr.io/x/ratchet-worker:sha"
    assert job["args"][-2:] == ["--spec", "/ratchet/spec.json"]
    (inject,) = job["injectedFiles"]
    assert base64.b64decode(inject["content"]) == b'{"a":1}'
    vol_in, vol_out = job["volumes"]
    assert (vol_in["containerPath"], vol_in["mode"], vol_in["sourcePath"]) == (
        "/in",
        "READ_ONLY",
        "batches/batch_42/in",
    )
    assert (vol_out["containerPath"], vol_out["mode"]) == ("/out", "READ_WRITE")
    assert vol_in["s3Config"]["endpoint"] == S3
    env = {e["name"]: e["value"] for e in job["environmentVariables"]}
    assert env["RATCHET_SANDBOX"] == "docker" and env["NEBIUS_TOKEN_FACTORY_KEY"]
    assert (job["platform"], job["preset"], job["timeout"]) == ("cpu-d3", "2vcpu-8gb", "2400s")


def test_oversized_spec_is_not_injected():
    body = build_job_request(_settings(), _spec(), prefix="p/", spec_json=b"x" * (64 * 1024 + 1))
    assert body["spec"]["injectedFiles"] == [] and "--spec" not in body["spec"]["args"]


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        ("PROVISIONING", "pending"),
        ("IMAGE_PULLING", "pending"),
        ("RUNNING", "running"),
        ("COMPLETED", "succeeded"),
        ("FAILED", "failed"),
        ("ERROR", "failed"),
        ("CANCELLED", "cancelled"),
        (None, "pending"),
        ("NEW_STATE", "pending"),
    ],
)
def test_state_mapping(raw, state):
    assert map_state(raw) == state


@respx.mock
async def test_submit_uploads_inputs_and_creates_job(tmp_path: Path):
    (tmp_path / "goldens").mkdir()
    (tmp_path / "spec.json").write_text("{}")
    (tmp_path / "goldens" / "run_a.json").write_text("{}")
    respx.get(f"{API}/ai/v1/jobs/by-name").respond(404)
    puts = respx.put(url__startswith=f"{S3}/ratchet-io/batches/batch_42/in/").respond(200)
    create = respx.post(f"{API}/ai/v1/jobs").respond(
        200, json={"id": "op-1", "resourceId": "aijob-9"}
    )
    runner = NebiusJobRunner(_settings(), sleep=_no_sleep)

    assert await runner.submit(_spec(), tmp_path) == "aijob-9"

    assert puts.call_count == 2
    assert {c.request.url.path for c in puts.calls} == {
        "/ratchet-io/batches/batch_42/in/spec.json",
        "/ratchet-io/batches/batch_42/in/goldens/run_a.json",
    }
    assert puts.calls[0].request.headers["Authorization"].startswith("AWS4-HMAC-SHA256 ")
    request = create.calls[0].request
    assert request.headers["Authorization"] == "Bearer iam-token-xyz"
    assert json.loads(request.content)["metadata"]["labels"] == {"ratchet-batch-id": "batch_42"}


@respx.mock
async def test_submit_is_idempotent_by_job_name(tmp_path: Path):
    respx.get(f"{API}/ai/v1/jobs/by-name").respond(200, json={"metadata": {"id": "aijob-1"}})
    create = respx.post(f"{API}/ai/v1/jobs").respond(200, json={"resourceId": "never"})
    runner = NebiusJobRunner(_settings(), sleep=_no_sleep)
    assert await runner.submit(_spec(), tmp_path) == "aijob-1"
    assert create.call_count == 0


@respx.mock
async def test_calls_retry_three_times_with_backoff_then_fail(tmp_path: Path):
    route = respx.get(f"{API}/ai/v1/jobs/by-name").respond(503)
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(UpstreamError):
        await NebiusJobRunner(_settings(), sleep=sleep).submit(_spec(), tmp_path)
    assert route.call_count == 4 and delays == [2.0, 4.0, 8.0]


@respx.mock
async def test_poll_reads_state_progress_and_exit_code():
    job = {
        "metadata": {"id": "aijob-9", "labels": {"ratchet-batch-id": "batch_42"}},
        "status": {"state": "FAILED"},
    }
    respx.get(f"{API}/ai/v1/jobs/aijob-9").respond(200, json=job)
    out = f"{S3}/ratchet-io/batches/batch_42/out"
    respx.get(f"{out}/progress.json").respond(
        200, json={"k": 1, "n": 2, "heartbeat_at": "2026-10-15T10:00:00+00:00"}
    )
    respx.get(f"{out}/exit.json").respond(200, json={"exit_code": 3})
    status = await NebiusJobRunner(_settings(), sleep=_no_sleep).poll("aijob-9")
    assert (status.state, status.k, status.n, status.exit_code) == ("failed", 1, 2, 3)
    assert status.heartbeat_at is not None and status.terminal


@respx.mock
async def test_fetch_downloads_the_output_prefix(tmp_path: Path):
    listing = (
        '<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        "<IsTruncated>false</IsTruncated>"
        "<Contents><Key>batches/batch_42/out/result.json</Key></Contents>"
        "<Contents><Key>batches/batch_42/out/replays/run_x/trace.json</Key></Contents>"
        "</ListBucketResult>"
    )
    respx.get(f"{S3}/ratchet-io", params={"list-type": "2"}).respond(200, text=listing)
    respx.get(f"{S3}/ratchet-io/batches/batch_42/out/result.json").respond(200, text="{}")
    respx.get(f"{S3}/ratchet-io/batches/batch_42/out/replays/run_x/trace.json").respond(
        200, text="t"
    )
    runner = NebiusJobRunner(_settings(), sleep=_no_sleep)
    runner._batches["aijob-9"] = "batch_42"
    out = await runner.fetch("aijob-9", tmp_path / "out")
    assert (out / "result.json").read_text() == "{}"
    assert (out / "replays" / "run_x" / "trace.json").read_text() == "t"


@respx.mock
async def test_cancel_posts_job_id():
    route = respx.post(f"{API}/ai/v1/jobs/cancel").respond(200, json={})
    await NebiusJobRunner(_settings(), sleep=_no_sleep).cancel("aijob-9")
    assert json.loads(route.calls[0].request.content) == {"id": "aijob-9"}


def test_sigv4_matches_the_aws_documented_get_object_example():
    headers = sigv4_headers(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        payload_hash="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        amz_date="20130524T000000Z",
        extra_headers={"Range": "bytes=0-9"},
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )


@respx.mock
async def test_s3_get_missing_object_is_none():
    respx.get(f"{S3}/b/k.json").respond(404)
    client = S3Client(
        endpoint=S3, region="eu-north1", bucket="b", access_key="a", secret_key="s", sleep=_no_sleep
    )
    assert await client.get_object("k.json") is None
    respx.get(f"{S3}/b/bad.json").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(UpstreamError):
        await client.get_object("bad.json")
