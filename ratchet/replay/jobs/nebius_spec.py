"""The ``POST /ai/v1/jobs`` body and the job-state mapping for Nebius Serverless Jobs (pure).

Field shapes follow the ``nebius.ai.v1.JobSpec`` OpenAPI schema and a job accepted by the
live API: ``args`` is one docker-run argument string (not a list), ``timeout`` is a Duration
string between ``3600s`` and ``604800s``, ``disk.sizeBytes`` is a string of bytes, injected
file ``content`` is base64 (≤ 64 KiB), and volumes mount either a managed bucket by id
(``storagebucket-…``, no credentials in the spec) or an external bucket as ``s3://<name>``
with inline ``s3Config`` credentials. The platform ignores ``sourcePath`` on ``s3://``
volumes (the bucket root is mounted), so the bucket is mounted once, read-write, at
``/bucket`` and the worker is pointed at the batch prefixes inside it.

``containerCommand`` is sent only when ``NEBIUS_JOB_CONTAINER_COMMAND`` is set: by default
the worker image's own entrypoint (``tini -- python /app/worker/entrypoint.py``) runs and
``args`` carries only the worker flags.
"""

from __future__ import annotations

import base64
from typing import Final

from ratchet.config.settings import Settings
from ratchet.errors import ConfigError
from ratchet.models import ReplayBatch
from ratchet.replay.jobs.base import JobState, job_name

__all__ = [
    "BATCH_LABEL",
    "BUCKET_MOUNT",
    "CONTAINER_FAILED_EXIT",
    "INJECT_LIMIT",
    "SPEC_PATH",
    "STATE_MAP",
    "TIMEOUT_MIN_S",
    "TIMEOUT_MAX_S",
    "WORKER_SCRIPT",
    "build_job_request",
    "exit_code_for_state",
    "is_bucket_id",
    "map_state",
    "s3_endpoint",
    "secret",
    "state_detail",
]

INJECT_LIMIT: Final = 64 * 1024
SPEC_PATH: Final = "/ratchet/spec.json"
BUCKET_MOUNT: Final = "/bucket"
WORKER_SCRIPT: Final = "/app/worker/entrypoint.py"
TIMEOUT_MIN_S: Final = 3600
TIMEOUT_MAX_S: Final = 604800
CPU_DISK_GIB: Final = 20
DISK_TYPE: Final = "NETWORK_SSD"
BATCH_LABEL: Final = "ratchet-batch-id"
# The worker's EXIT_SANDBOX: the container died before writing its own exit.json.
CONTAINER_FAILED_EXIT: Final = 4
_GIB: Final = 1024**3

STATE_MAP: dict[str, JobState] = {
    "STATE_UNSPECIFIED": "pending",
    "PROVISIONING": "pending",
    "STARTING": "pending",
    "IMAGE_PULLING": "pending",
    "RUNNING": "running",
    "COMPLETING": "running",
    "CANCELLING": "running",
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "ERROR": "failed",
    "CANCELLED": "cancelled",
    "DELETING": "cancelled",
}


def map_state(raw: str | None) -> JobState:
    return STATE_MAP.get(raw or "STATE_UNSPECIFIED", "pending")


def state_detail(job: object) -> str | None:
    """``status.stateDetails.code`` (``StartFailed``, ``ContainerFailed``,
    ``TimeoutExceeded``, ``NotEnoughResources``) or None; never the free-text message."""
    status = job.get("status") if isinstance(job, dict) else None
    details = status.get("stateDetails") if isinstance(status, dict) else None
    code = details.get("code") if isinstance(details, dict) else None
    return str(code) if code else None


def exit_code_for_state(state: JobState, detail: str | None) -> int | None:
    """The exit code when the worker left no ``exit.json``: the platform has no exit-code
    field, so ``COMPLETED`` is 0 and a failed container is nonzero."""
    if state == "succeeded":
        return 0
    if state == "failed" and detail == "ContainerFailed":
        return CONTAINER_FAILED_EXIT
    return None


def secret(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter()) if callable(getter) else ""


def s3_endpoint(settings: Settings) -> str:
    return settings.nebius_s3_endpoint or f"https://storage.{settings.nebius_region}.nebius.cloud"


def is_bucket_id(value: str | None) -> bool:
    return bool(value) and str(value).startswith("storagebucket-")


def _platform(settings: Settings) -> tuple[str, str]:
    raw = settings.nebius_job_preset or "cpu-d3:2vcpu-8gb"
    platform, sep, preset = raw.replace("/", ":").partition(":")
    return (platform, preset) if sep else ("cpu-d3", platform)


def _env(settings: Settings, spec: ReplayBatch) -> list[dict[str, str]]:
    values = {
        "RATCHET_SANDBOX": spec.sandbox,
        "RATCHET_HARNESS": spec.harness,
        "RATCHET_UPSTREAM": settings.ratchet_upstream,
        "RATCHET_AGENT_TIER": spec.agent_tier,
        "RATCHET_WORKER_IMAGE": spec.worker_image,
        "NEBIUS_TF_BASE_URL": settings.nebius_tf_base_url,
        "RATCHET_TF_PROXY_UPSTREAM": settings.ratchet_tf_proxy_upstream
        or settings.nebius_tf_base_url,
        "NEBIUS_TOKEN_FACTORY_KEY": secret(settings.nebius_token_factory_key),
    }
    for tier in ("nano", "super", "ultra"):
        override = getattr(settings, f"ratchet_model_{tier}")
        if override:
            values[f"RATCHET_MODEL_{tier.upper()}"] = override
    return [{"name": k, "value": str(v)} for k, v in sorted(values.items()) if v]


def _volumes(settings: Settings) -> list[dict[str, object]]:
    bucket_id = settings.nebius_s3_bucket_id or (
        settings.nebius_s3_bucket if is_bucket_id(settings.nebius_s3_bucket) else None
    )
    base: dict[str, object]
    if bucket_id:
        base = {"source": bucket_id}
    else:
        base = {
            "source": f"s3://{settings.nebius_s3_bucket}",
            "s3Config": {
                "endpoint": s3_endpoint(settings),
                "region": settings.nebius_region,
                "credentials": {
                    "accessKeyId": secret(settings.nebius_s3_access_key_id),
                    "secretAccessKey": secret(settings.nebius_s3_secret_access_key),
                },
            },
        }
    return [{**base, "containerPath": BUCKET_MOUNT, "mode": "READ_WRITE"}]


def _timeout(timeout_s: int) -> str:
    return f"{min(TIMEOUT_MAX_S, max(TIMEOUT_MIN_S, timeout_s + 1800))}s"


def _disk(settings: Settings, platform: str) -> dict[str, str] | None:
    gib = settings.nebius_job_disk_gib
    if gib is None and platform.startswith("cpu"):
        gib = CPU_DISK_GIB
    return {"type": DISK_TYPE, "sizeBytes": str(gib * _GIB)} if gib else None


def build_job_request(
    settings: Settings, spec: ReplayBatch, *, prefix: str, spec_json: bytes
) -> dict[str, object]:
    """The ``POST /ai/v1/jobs`` body for one batch."""
    if not settings.nebius_project_id or not settings.nebius_s3_bucket:
        raise ConfigError(
            "NEBIUS_PROJECT_ID and NEBIUS_S3_BUCKET are required", code="config_error"
        )
    root = f"{BUCKET_MOUNT}/{prefix.strip('/')}"
    flags = ["--inputs", f"{root}/in", "--output", f"{root}/out"]
    injected: list[dict[str, str]] = []
    if 0 < len(spec_json) <= INJECT_LIMIT:
        injected.append(
            {"containerPath": SPEC_PATH, "content": base64.b64encode(spec_json).decode()}
        )
        flags += ["--spec", SPEC_PATH]
    command = settings.nebius_job_container_command
    args = " ".join([WORKER_SCRIPT, *flags] if command else flags)
    platform, preset = _platform(settings)
    job: dict[str, object] = {"image": spec.worker_image}
    if command:
        job["containerCommand"] = command
    job |= {
        "args": args,
        "environmentVariables": _env(settings, spec),
        "injectedFiles": injected,
        "volumes": _volumes(settings),
        "platform": platform,
        "preset": preset,
        "timeout": _timeout(spec.timeout_s),
        "restartAttempts": "0",
        "publicIp": False,
    }
    disk = _disk(settings, platform)
    if disk:
        job["disk"] = disk
    if settings.nebius_job_subnet_id:
        job["subnetId"] = settings.nebius_job_subnet_id
    return {
        "metadata": {
            "parentId": settings.nebius_project_id,
            "name": job_name(spec.batch_id),
            "labels": {BATCH_LABEL: spec.batch_id},
        },
        "spec": job,
    }
