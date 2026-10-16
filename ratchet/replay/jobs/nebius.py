"""NebiusJobRunner: one Nebius Serverless AI Job per batch.

Flow: upload the packed inputs to ``s3://<bucket>/batches/<batch_id>/in/`` → ``POST
/ai/v1/jobs`` with the worker image, the bucket mounted read-only at ``/in`` and read-write
at ``/out`` (``sourcePath`` = the batch prefixes), ``spec.json`` also injected as a file
(≤ 64 KiB) → poll ``GET /ai/v1/jobs/{id}`` (``status.state``) plus the worker's
``progress.json`` / ``exit.json`` in the bucket → download ``…/out/`` on fetch.

Idempotency: the job is named ``ratchet-<batch_id>`` and looked up by name before create;
``batch_id`` is also sent as an idempotency header. Every call: 30 s timeout, 3 retries at
2/4/8 s on 5xx/429/transport errors. Auth: ``Authorization: Bearer <IAM token>``
(``NEBIUS_API_KEY`` holds the token; AI Cloud has no API keys).

Unverified field names and behaviours, to verify against a live account:

1. ``GET /ai/v1/jobs/by-name?parentId=&name=`` exists for GetByName (REST path form).
2. ``X-Idempotency-Key`` is honoured (else name lookup is the only dedupe).
3. ``spec.containerCommand`` + ``spec.args`` override the image ENTRYPOINT.
4. ``spec.injectedFiles[].content`` is base64 in REST JSON (proto ``bytes``).
5. ``spec.volumes[]`` with ``source: "s3://<bucket>"``, ``sourcePath``, ``mode``,
   ``s3Config.{endpoint,region,credentials.{accessKeyId,secretAccessKey}}``; ``sourcePath``
   selects a key prefix inside the bucket.
6. ``spec.timeout`` as a protobuf Duration string (``"2400s"``); ``restartAttempts`` as a
   string int64.
7. Create returns an Operation whose ``resourceId`` is the job id.
8. ``POST /ai/v1/jobs/cancel`` takes ``{"id": ...}``.
9. ``metadata.labels`` values may contain ``_`` (the batch id label).
10. Secrets: ``NEBIUS_TOKEN_FACTORY_KEY`` is passed as a plain env value; switch to
    ``mysteryboxSecret`` once a MysteryBox secret id is configured.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ConfigError, NotFoundError, UpstreamError
from ratchet.models import ReplayBatch
from ratchet.replay.jobs.base import JobState, JobStatus, job_name, parse_progress
from ratchet.replay.jobs.s3 import S3Client, Sleep, send_with_retries

__all__ = ["NebiusJobRunner", "build_job_request", "map_state", "STATE_MAP"]

DEFAULT_API = "https://api.nebius.cloud"
INJECT_LIMIT = 64 * 1024
SPEC_PATH = "/ratchet/spec.json"
STATE_MAP: dict[str, JobState] = {
    "STATE_UNSPECIFIED": "pending",
    "PROVISIONING": "pending",
    "STARTING": "pending",
    "IMAGE_PULLING": "pending",
    "RUNNING": "running",
    "CANCELLING": "running",
    "COMPLETED": "succeeded",
    "FAILED": "failed",
    "ERROR": "failed",
    "CANCELLED": "cancelled",
    "DELETING": "cancelled",
}
_BATCH_LABEL = "ratchet-batch-id"


def map_state(raw: str | None) -> JobState:
    return STATE_MAP.get(raw or "STATE_UNSPECIFIED", "pending")


def _secret(value: object) -> str:
    getter = getattr(value, "get_secret_value", None)
    return str(getter()) if callable(getter) else ""


def _platform(settings: Settings) -> tuple[str, str]:
    raw = settings.nebius_job_preset or "cpu-d3:2vcpu-8gb"
    platform, sep, preset = raw.replace("/", ":").partition(":")
    return (platform, preset) if sep else ("cpu-d3", platform)


def _env(settings: Settings, spec: ReplayBatch) -> list[dict[str, object]]:
    values = {
        "RATCHET_SANDBOX": spec.sandbox,
        "RATCHET_HARNESS": spec.harness,
        "RATCHET_UPSTREAM": settings.ratchet_upstream,
        "RATCHET_AGENT_TIER": spec.agent_tier,
        "RATCHET_WORKER_IMAGE": spec.worker_image,
        "NEBIUS_TF_BASE_URL": settings.nebius_tf_base_url,
        "RATCHET_TF_PROXY_UPSTREAM": settings.ratchet_tf_proxy_upstream
        or settings.nebius_tf_base_url,
        "NEBIUS_TOKEN_FACTORY_KEY": _secret(settings.nebius_token_factory_key),
    }
    for tier in ("nano", "super", "ultra"):
        override = getattr(settings, f"ratchet_model_{tier}")
        if override:
            values[f"RATCHET_MODEL_{tier.upper()}"] = override
    return [{"name": k, "value": v} for k, v in sorted(values.items()) if v]


def build_job_request(
    settings: Settings, spec: ReplayBatch, *, prefix: str, spec_json: bytes
) -> dict[str, object]:
    """The ``POST /ai/v1/jobs`` body for one batch (pure)."""
    if not settings.nebius_project_id or not settings.nebius_s3_bucket:
        raise ConfigError(
            "NEBIUS_PROJECT_ID and NEBIUS_S3_BUCKET are required", code="config_error"
        )
    s3 = {
        "endpoint": _s3_endpoint(settings),
        "region": settings.nebius_region,
        "credentials": {
            "accessKeyId": _secret(settings.nebius_s3_access_key_id),
            "secretAccessKey": _secret(settings.nebius_s3_secret_access_key),
        },
    }

    def volume(path: str, sub: str, mode: str) -> dict[str, object]:
        return {
            "source": f"s3://{settings.nebius_s3_bucket}",
            "sourcePath": f"{prefix}{sub}",
            "containerPath": path,
            "mode": mode,
            "s3Config": s3,
        }

    args = ["/app/worker/entrypoint.py", "--inputs", "/in", "--output", "/out"]
    injected: list[dict[str, object]] = []
    if len(spec_json) <= INJECT_LIMIT:
        injected.append(
            {"containerPath": SPEC_PATH, "content": base64.b64encode(spec_json).decode()}
        )
        args += ["--spec", SPEC_PATH]
    platform, preset = _platform(settings)
    return {
        "metadata": {
            "parentId": settings.nebius_project_id,
            "name": job_name(spec.batch_id),
            "labels": {_BATCH_LABEL: spec.batch_id},
        },
        "spec": {
            "image": spec.worker_image,
            "containerCommand": "python",
            "args": args,
            "environmentVariables": _env(settings, spec),
            "injectedFiles": injected,
            "volumes": [volume("/in", "in", "READ_ONLY"), volume("/out", "out", "READ_WRITE")],
            "platform": platform,
            "preset": preset,
            "timeout": f"{spec.timeout_s + 1800}s",
            "restartAttempts": "0",
            "publicIp": False,
        },
    }


def _s3_endpoint(settings: Settings) -> str:
    return settings.nebius_s3_endpoint or f"https://storage.{settings.nebius_region}.nebius.cloud"


class NebiusJobRunner:
    name = "nebius"

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        s3: S3Client | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._transport = transport
        self._sleep = sleep
        self._s3 = s3 or S3Client(
            endpoint=_s3_endpoint(self._settings),
            region=self._settings.nebius_region,
            bucket=self._settings.nebius_s3_bucket or "",
            access_key=_secret(self._settings.nebius_s3_access_key_id),
            secret_key=_secret(self._settings.nebius_s3_secret_access_key),
            transport=transport,
            sleep=sleep,
        )
        self._batches: dict[str, str] = {}  # job id -> batch id

    @staticmethod
    def prefix(batch_id: str) -> str:
        return f"batches/{batch_id}/"

    async def _call(
        self, method: str, path: str, *, body: object = None, params: dict[str, str] | None = None
    ) -> httpx.Response:
        token = _secret(self._settings.nebius_api_key)
        if not token:
            raise ConfigError("NEBIUS_API_KEY (IAM token) is not set", code="config_error")
        base = self._settings.nebius_jobs_api_url or DEFAULT_API
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(
            base_url=base, transport=self._transport, timeout=30.0, headers=headers
        ) as client:
            return await send_with_retries(
                lambda: client.request(method, path, json=body, params=params),
                sleep=self._sleep,
                what="Nebius Jobs API",
            )

    async def _find(self, batch_id: str) -> str | None:
        params = {"parentId": self._settings.nebius_project_id or "", "name": job_name(batch_id)}
        response = await self._call("GET", "/ai/v1/jobs/by-name", params=params)
        if response.status_code == 404:
            return None
        _ok(response, "get job by name")
        job_id = response.json().get("metadata", {}).get("id")
        return str(job_id) if job_id else None

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        existing = await self._find(spec.batch_id)
        if existing:
            self._batches[existing] = spec.batch_id
            return existing
        prefix = self.prefix(spec.batch_id)
        await self._s3.upload_dir(inputs_dir, f"{prefix}in/")
        spec_json = spec.model_dump_json().encode("utf-8")
        body = build_job_request(self._settings, spec, prefix=prefix, spec_json=spec_json)
        response = await self._call("POST", "/ai/v1/jobs", body=body)
        _ok(response, "create job")
        operation = response.json()
        job_id = operation.get("resourceId") or operation.get("resource_id")
        if not job_id:
            raise UpstreamError("create job returned no resourceId", retryable=False)
        self._batches[str(job_id)] = spec.batch_id
        return str(job_id)

    async def _batch_of(self, job_id: str, job: dict[str, object] | None = None) -> str:
        if job_id in self._batches:
            return self._batches[job_id]
        if job is None:
            response = await self._call("GET", f"/ai/v1/jobs/{job_id}")
            _ok(response, "get job")
            job = response.json()
        metadata = job.get("metadata") if isinstance(job, dict) else None
        labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
        batch_id = labels.get(_BATCH_LABEL) if isinstance(labels, dict) else None
        if not batch_id:
            raise NotFoundError(f"job {job_id} is not a ratchet batch", code="not_found")
        self._batches[job_id] = str(batch_id)
        return str(batch_id)

    async def poll(self, job_id: str) -> JobStatus:
        response = await self._call("GET", f"/ai/v1/jobs/{job_id}")
        if response.status_code == 404:
            raise NotFoundError(f"job {job_id} not found", code="not_found")
        _ok(response, "get job")
        job = response.json()
        state = map_state(job.get("status", {}).get("state"))
        out = f"{self.prefix(await self._batch_of(job_id, job))}out/"
        k, n, beat = parse_progress(await self._s3.get_object(f"{out}progress.json"))
        exit_code: int | None = None
        if state in {"succeeded", "failed", "cancelled"}:
            raw = await self._s3.get_object(f"{out}exit.json")
            exit_code = _exit_code(raw) if raw else (0 if state == "succeeded" else None)
        if state == "succeeded" and exit_code not in (None, 0):
            state = "failed"
        return JobStatus(state, k, n, beat, exit_code)

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        prefix = f"{self.prefix(await self._batch_of(job_id))}out/"
        output_dir.mkdir(parents=True, exist_ok=True)
        await self._s3.download_prefix(prefix, output_dir)
        return output_dir

    async def cancel(self, job_id: str) -> None:
        response = await self._call("POST", "/ai/v1/jobs/cancel", body={"id": job_id})
        if response.status_code not in (404, 409):
            _ok(response, "cancel job")


def _ok(response: httpx.Response, what: str) -> None:
    if response.status_code >= 400:
        raise UpstreamError(
            f"Nebius {what} returned {response.status_code}",
            retryable=False,
            context={"status": response.status_code},
        )


def _exit_code(raw: bytes) -> int | None:
    try:
        return int(json.loads(raw)["exit_code"])
    except (ValueError, KeyError, TypeError):
        return None
