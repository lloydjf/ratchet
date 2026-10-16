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
    values: dict[str, object] = {
        "nebius_api_key": "iam-token-xyz",
        "nebius_project_id": "project-e00abc",
        "nebius_s3_bucket": "ratchet-io",
        "nebius_s3_access_key_id": "AKIDEXAMPLE",
        "nebius_s3_secret_access_key": "s3secret0123456789",
        "nebius_token_factory_key": "tf-key-0123456789",
    }
    return Settings(_env_file=None, **{**values, **extra})  # type: ignore[call-arg, arg-type]


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


# ``nebius.ai.v1.JobSpec`` properties in the published OpenAPI document.
JOBSPEC_FIELDS = {
    "args", "containerCommand", "disk", "environmentVariables", "image", "injectedFiles",
    "platform", "ports", "preemptible", "preset", "pricingModel", "publicIp",
    "registryCredentials", "restartAttempts", "shmSizeBytes", "sshAuthorizedKeys",
    "subnetId", "timeout", "volumes", "workingDir",
}  # fmt: skip
ARGS = (
    "--inputs /bucket/batches/batch_42/in --output /bucket/batches/batch_42/out"
    " --spec /ratchet/spec.json"
)
SPEC_B64 = base64.b64encode(b'{"a":1}').decode()


def _body(settings: Settings, spec: ReplayBatch | None = None) -> dict:
    return build_job_request(
        settings, spec or _spec(), prefix="batches/batch_42/", spec_json=b'{"a":1}'
    )


def test_job_request_matches_the_accepted_spec_with_an_external_bucket():
    body = _body(_settings(nebius_job_subnet_id="vpcsubnet-e00xyz"))
    assert body["metadata"] == {
        "parentId": "project-e00abc",
        "name": "ratchet-batch-42",
        "labels": {"ratchet-batch-id": "batch_42"},
    }
    job = dict(body["spec"])
    env = {e["name"]: e["value"] for e in job.pop("environmentVariables")}
    s3 = {
        "endpoint": S3,
        "region": "eu-north1",
        "credentials": {"accessKeyId": "AKIDEXAMPLE", "secretAccessKey": "s3secret0123456789"},
    }
    assert job == {
        "image": "ghcr.io/x/ratchet-worker:sha",
        "args": ARGS,
        "injectedFiles": [{"containerPath": "/ratchet/spec.json", "content": SPEC_B64}],
        "volumes": [
            {
                "source": "s3://ratchet-io",
                "containerPath": "/bucket",
                "mode": "READ_WRITE",
                "s3Config": s3,
            }
        ],
        "platform": "cpu-d3",
        "preset": "2vcpu-8gb",
        "timeout": "3600s",
        "restartAttempts": "0",
        "publicIp": False,
        "disk": {"type": "NETWORK_SSD", "sizeBytes": "21474836480"},
        "subnetId": "vpcsubnet-e00xyz",
    }
    assert env["RATCHET_SANDBOX"] == "docker" and env["NEBIUS_TOKEN_FACTORY_KEY"]
    assert all(set(e) == {"name", "value"} for e in body["spec"]["environmentVariables"])
    assert set(body["spec"]) <= JOBSPEC_FIELDS
    assert isinstance(body["spec"]["args"], str)


@pytest.mark.parametrize(
    "extra",
    [
        {"nebius_s3_bucket_id": "storagebucket-e00abc"},
        {"nebius_s3_bucket": "storagebucket-e00abc"},
    ],
)
def test_managed_bucket_id_is_mounted_without_credentials(extra):
    body = _body(_settings(**extra))
    assert body["spec"]["volumes"] == [
        {"source": "storagebucket-e00abc", "containerPath": "/bucket", "mode": "READ_WRITE"}
    ]
    assert body["spec"]["args"] == ARGS
    assert "s3secret0123456789" not in json.dumps(body["spec"]["volumes"])


@pytest.mark.parametrize(
    ("timeout_s", "expected"),
    [(600, "3600s"), (1800, "3600s"), (3000, "4800s"), (10**6, "604800s")],
)
def test_timeout_is_clamped_to_the_platform_range(timeout_s, expected):
    spec = _spec().model_copy(update={"timeout_s": timeout_s})
    assert _body(_settings(), spec)["spec"]["timeout"] == expected


def test_disk_defaults_to_20_gib_on_cpu_and_is_overridable():
    gib = 1024**3
    assert _body(_settings())["spec"]["disk"]["sizeBytes"] == str(20 * gib)
    big = _body(_settings(nebius_job_disk_gib=50))["spec"]["disk"]
    assert big == {"type": "NETWORK_SSD", "sizeBytes": str(50 * gib)}
    gpu = _body(_settings(nebius_job_preset="gpu-h100-sxm:1gpu-16vcpu-200gb"))["spec"]
    assert (gpu["platform"], gpu["preset"]) == ("gpu-h100-sxm", "1gpu-16vcpu-200gb")
    assert "disk" not in gpu


def test_image_entrypoint_is_kept_unless_a_container_command_is_set():
    assert "containerCommand" not in _body(_settings())["spec"]
    job = _body(_settings(nebius_job_container_command="/app/.venv/bin/python"))["spec"]
    assert job["containerCommand"] == "/app/.venv/bin/python"
    assert job["args"] == f"/app/worker/entrypoint.py {ARGS}"


def test_oversized_spec_is_not_injected():
    body = build_job_request(_settings(), _spec(), prefix="p/", spec_json=b"x" * (64 * 1024 + 1))
    assert body["spec"]["injectedFiles"] == []
    assert body["spec"]["args"] == "--inputs /bucket/p/in --output /bucket/p/out"


@pytest.mark.parametrize(
    ("raw", "state"),
    [
        ("PROVISIONING", "pending"),
        ("IMAGE_PULLING", "pending"),
        ("RUNNING", "running"),
        ("COMPLETING", "running"),
        ("CANCELLING", "running"),
        ("COMPLETED", "succeeded"),
        ("FAILED", "failed"),
        ("ERROR", "failed"),
        ("CANCELLED", "cancelled"),
        ("DELETING", "cancelled"),
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
    respx.get(f"{API}/ai/v1/jobs/get-by-name").respond(404)
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
    found = {"metadata": {"id": "aijob-1"}, "status": {"state": "RUNNING"}}
    lookup = respx.get(f"{API}/ai/v1/jobs/get-by-name").respond(200, json=found)
    create = respx.post(f"{API}/ai/v1/jobs").respond(200, json={"resourceId": "never"})
    runner = NebiusJobRunner(_settings(), sleep=_no_sleep)
    assert await runner.submit(_spec(), tmp_path) == "aijob-1"
    assert create.call_count == 0
    assert dict(lookup.calls[0].request.url.params) == {
        "parentId": "project-e00abc",
        "name": "ratchet-batch-42",
    }
    assert runner.gate.in_flight == {"aijob-1"}


@respx.mock
async def test_calls_retry_three_times_with_backoff_then_fail(tmp_path: Path):
    route = respx.get(f"{API}/ai/v1/jobs/get-by-name").respond(503)
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
