"""The ``JobRunner`` protocol and ``JobStatus`` (CONTRACTS §5)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from ratchet import clock
from ratchet.models import ReplayBatch

__all__ = [
    "JobState",
    "JobStatus",
    "JobRunner",
    "TERMINAL_STATES",
    "parse_progress",
    "job_name",
    "read_exit_code",
]

JobState = Literal["pending", "running", "succeeded", "failed", "cancelled"]
TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


@dataclass(frozen=True)
class JobStatus:
    """``exit_code`` is the worker's exit code (0/3/4/5/6) once known; the orchestrator
    switches on it. ``succeeded`` means exit 0; a partial batch (3) is ``failed`` with its
    result still fetchable."""

    state: JobState
    k: int
    n: int
    heartbeat_at: datetime | None
    exit_code: int | None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES


class JobRunner(Protocol):
    name: str

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        """Start the batch; idempotent by ``batch_id`` (a resubmit returns the same job)."""
        ...

    async def poll(self, job_id: str) -> JobStatus: ...

    async def fetch(self, job_id: str, output_dir: Path) -> Path: ...

    async def cancel(self, job_id: str) -> None: ...


def job_name(batch_id: str) -> str:
    return f"ratchet-{batch_id.replace('_', '-')}"


def parse_progress(raw: bytes | str | None) -> tuple[int, int, datetime | None]:
    """``progress.json`` → ``(k, n, heartbeat_at)``; tolerant of missing or partial files."""
    if not raw:
        return 0, 0, None
    try:
        data = json.loads(raw)
        beat = data.get("heartbeat_at")
        return int(data.get("k", 0)), int(data.get("n", 0)), clock.parse(beat) if beat else None
    except (ValueError, TypeError, AttributeError):
        return 0, 0, None


def read_exit_code(path: Path) -> int | None:
    try:
        return int(json.loads(path.read_text(encoding="utf-8"))["exit_code"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
