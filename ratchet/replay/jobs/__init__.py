"""Job runners (CONTRACTS §5): ``JobRunner`` protocol, ``JobStatus``, ``LocalJobRunner``,
``NebiusJobRunner`` and ``get_runner(settings)``."""

from __future__ import annotations

from ratchet.config.settings import Settings, get_settings
from ratchet.replay.jobs.base import TERMINAL_STATES, JobRunner, JobState, JobStatus
from ratchet.replay.jobs.local import LocalJobRunner
from ratchet.replay.jobs.nebius import NebiusJobRunner

__all__ = [
    "JobRunner",
    "JobState",
    "JobStatus",
    "TERMINAL_STATES",
    "LocalJobRunner",
    "NebiusJobRunner",
    "get_runner",
]


def get_runner(settings: Settings | None = None) -> JobRunner:
    """``RATCHET_JOB_RUNNER=nebius|local`` (local mode from ``RATCHET_LOCAL_RUNNER_MODE``)."""
    settings = settings or get_settings()
    if settings.ratchet_job_runner == "nebius":
        return NebiusJobRunner(settings)
    return LocalJobRunner(settings.ratchet_local_runner_mode, settings=settings)
