"""NebiusJobRunner: one Nebius Serverless AI Job per batch.

Flow: upload the packed inputs to ``<bucket>/batches/<batch_id>/in/`` → wait for a job slot
(``SubmitGate``) → ``POST /ai/v1/jobs`` (body from ``nebius_spec.build_job_request``: the
worker image, the bucket mounted read-only at ``/in`` and read-write at ``/out``,
``spec.json`` injected as a file) → poll ``GET /ai/v1/jobs/{id}`` (``status.state`` and
``status.stateDetails.code``) plus the worker's ``progress.json`` / ``exit.json`` in the
bucket → download ``…/out/`` on fetch.

Restart safety: the job is named ``ratchet-<batch_id>`` and looked up with
``GET /ai/v1/jobs/get-by-name?parentId=&name=`` before create. Every call: 30 s timeout,
3 retries at 2/4/8 s on 5xx/429/transport errors, except create, where a quota-exhausted
answer (HTTP 429 / ``RESOURCE_EXHAUSTED``: every job is a VM on the
``compute.instance.count`` quota) is retried with the gate's capped backoff for up to
``RATCHET_JOBS_SUBMIT_WAIT_S``. At most ``RATCHET_JOBS_MAX_CONCURRENT`` jobs run at once;
while full, the gate counts unfinished ratchet jobs with ``GET /ai/v1/jobs?parentId=``.
Auth: ``Authorization: Bearer <IAM token>`` (``NEBIUS_API_KEY`` holds the token).

Secrets: ``NEBIUS_TOKEN_FACTORY_KEY`` is passed as a plain env value, and an external
(``s3://``) bucket mount carries the S3 key inline; a managed bucket id
(``NEBIUS_S3_BUCKET_ID``) needs no key in the spec.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ConfigError, NotFoundError, UpstreamError
from ratchet.log import get_logger
from ratchet.models import ReplayBatch
from ratchet.replay.jobs.base import TERMINAL_STATES, JobStatus, job_name, parse_progress
from ratchet.replay.jobs.gate import Backoff, SubmitGate
from ratchet.replay.jobs.nebius_spec import (
    BATCH_LABEL,
    STATE_MAP,
    build_job_request,
    exit_code_for_state,
    map_state,
    s3_endpoint,
    secret,
    state_detail,
)
from ratchet.replay.jobs.s3 import S3Client, Sleep, send_with_retries

__all__ = [
    "NebiusJobRunner",
    "build_job_request",
    "map_state",
    "STATE_MAP",
    "is_quota_exhausted",
]

DEFAULT_API = "https://api.nebius.cloud"
_LIST_PAGE = 100
_LIST_MAX_PAGES = 20

_log = get_logger("replay.jobs.nebius")


def is_quota_exhausted(response: httpx.Response) -> bool:
    """HTTP 429, or a gRPC ``RESOURCE_EXHAUSTED`` (code 8) / quota error body."""
    if response.status_code == 429:
        return True
    if response.status_code < 400:
        return False
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("code") in (8, "8", "RESOURCE_EXHAUSTED"):
        return True
    text = response.text.lower()
    return "resourceexhausted" in text or "resource_exhausted" in text or "quota limit" in text


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
            endpoint=s3_endpoint(self._settings),
            region=self._settings.nebius_region,
            bucket=self._settings.nebius_s3_bucket or "",
            access_key=secret(self._settings.nebius_s3_access_key_id),
            secret_key=secret(self._settings.nebius_s3_secret_access_key),
            transport=transport,
            sleep=sleep,
        )
        self._batches: dict[str, str] = {}  # job id -> batch id
        self.gate = SubmitGate(self._settings.ratchet_jobs_max_concurrent, refresh=self._live)

    @staticmethod
    def prefix(batch_id: str) -> str:
        return f"batches/{batch_id}/"

    async def _call(
        self,
        method: str,
        path: str,
        *,
        body: object = None,
        params: dict[str, str] | None = None,
        retry_429: bool = True,
    ) -> httpx.Response:
        token = secret(self._settings.nebius_api_key)
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
                retry_429=retry_429,
            )

    async def _find(self, batch_id: str) -> tuple[str, bool] | None:
        """``(job id, unfinished)`` of the batch's job, or None."""
        params = {"parentId": self._settings.nebius_project_id or "", "name": job_name(batch_id)}
        response = await self._call("GET", "/ai/v1/jobs/get-by-name", params=params)
        if response.status_code == 404:
            return None
        _ok(response, "get job by name")
        job = response.json()
        job_id = job.get("metadata", {}).get("id")
        if not job_id:
            return None
        state = map_state(job.get("status", {}).get("state"))
        return str(job_id), state not in TERMINAL_STATES

    async def _live(self) -> set[str] | None:
        """Ids of the project's unfinished ratchet jobs, or None when listing fails."""
        live: set[str] = set()
        params = {"parentId": self._settings.nebius_project_id or "", "pageSize": str(_LIST_PAGE)}
        try:
            for _ in range(_LIST_MAX_PAGES):
                response = await self._call("GET", "/ai/v1/jobs", params=params)
                _ok(response, "list jobs")
                page = response.json()
                for job in page.get("items") or []:
                    metadata = job.get("metadata") or {}
                    labeled = BATCH_LABEL in (metadata.get("labels") or {})
                    state = map_state((job.get("status") or {}).get("state"))
                    if labeled and metadata.get("id") and state not in TERMINAL_STATES:
                        live.add(str(metadata["id"]))
                token = page.get("nextPageToken")
                if not token:
                    return live
                params = {**params, "pageToken": str(token)}
        except (UpstreamError, ValueError, AttributeError):
            _log.warning("jobs.list_failed", code="upstream_unavailable")
            return None
        return live

    async def _create(self, body: dict[str, object], batch_id: str, backoff: Backoff) -> str:
        while True:
            response = await self._call("POST", "/ai/v1/jobs", body=body, retry_429=False)
            if not is_quota_exhausted(response):
                break
            _log.warning(
                "jobs.submit_waiting",
                batch_id=batch_id,
                code="quota_exhausted",
                status=response.status_code,
                waited_s=backoff.waited_s,
            )
            if not await backoff.pause():
                _log.warning("jobs.submit_gave_up", batch_id=batch_id, code="quota_exhausted")
                raise UpstreamError(
                    "Nebius compute quota stayed exhausted",
                    code="upstream_unavailable",
                    context={"reason": "quota_exhausted", "batch_id": batch_id},
                )
        _ok(response, "create job")
        operation = response.json()
        job_id = operation.get("resourceId") or operation.get("resource_id")
        if not job_id:
            raise UpstreamError("create job returned no resourceId", retryable=False)
        return str(job_id)

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        existing = await self._find(spec.batch_id)
        if existing:
            job_id, unfinished = existing
            self._batches[job_id] = spec.batch_id
            if unfinished:
                self.gate.track(job_id)
            return job_id
        prefix = self.prefix(spec.batch_id)
        await self._s3.upload_dir(inputs_dir, f"{prefix}in/")
        spec_json = spec.model_dump_json().encode("utf-8")
        body = build_job_request(self._settings, spec, prefix=prefix, spec_json=spec_json)
        backoff = Backoff(self._settings.ratchet_jobs_submit_wait_s, self._sleep)
        await self.gate.acquire(backoff, batch_id=spec.batch_id)
        created: str | None = None
        try:
            created = await self._create(body, spec.batch_id, backoff)
        finally:
            self.gate.settle(created)
        self._batches[created] = spec.batch_id
        return created

    async def _batch_of(self, job_id: str, job: dict[str, object] | None = None) -> str:
        if job_id in self._batches:
            return self._batches[job_id]
        if job is None:
            response = await self._call("GET", f"/ai/v1/jobs/{job_id}")
            _ok(response, "get job")
            job = response.json()
        metadata = job.get("metadata") if isinstance(job, dict) else None
        labels = metadata.get("labels", {}) if isinstance(metadata, dict) else {}
        batch_id = labels.get(BATCH_LABEL) if isinstance(labels, dict) else None
        if not batch_id:
            raise NotFoundError(f"job {job_id} is not a ratchet batch", code="not_found")
        self._batches[job_id] = str(batch_id)
        return str(batch_id)

    async def poll(self, job_id: str) -> JobStatus:
        response = await self._call("GET", f"/ai/v1/jobs/{job_id}")
        if response.status_code == 404:
            self.gate.release(job_id)
            raise NotFoundError(f"job {job_id} not found", code="not_found")
        _ok(response, "get job")
        job = response.json()
        raw_state = (job.get("status") or {}).get("state")
        state = map_state(raw_state)
        detail = state_detail(job)
        out = f"{self.prefix(await self._batch_of(job_id, job))}out/"
        k, n, beat = parse_progress(await self._s3.get_object(f"{out}progress.json"))
        exit_code: int | None = None
        if state in TERMINAL_STATES:
            raw = await self._s3.get_object(f"{out}exit.json")
            exit_code = _exit_code(raw) if raw else None
            if exit_code is None:
                exit_code = exit_code_for_state(state, detail)
            if self.gate.release(job_id):
                _log.info("jobs.finished", job_id=job_id, state=raw_state, code=detail)
        if state == "succeeded" and exit_code not in (None, 0):
            state = "failed"
        return JobStatus(state, k, n, beat, exit_code, detail)

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        prefix = f"{self.prefix(await self._batch_of(job_id))}out/"
        output_dir.mkdir(parents=True, exist_ok=True)
        await self._s3.download_prefix(prefix, output_dir)
        return output_dir

    async def cancel(self, job_id: str) -> None:
        response = await self._call("POST", "/ai/v1/jobs/cancel", body={"id": job_id})
        self.gate.release(job_id)
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
