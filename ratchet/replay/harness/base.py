"""The harness adapter interface and the pieces both adapters share.

``HarnessAdapter`` is the adapter protocol itself. ``SandboxedHarness`` adds the two
methods the replay worker needs to run an adapter *inside* a sandbox instead of in the
current process: :meth:`plan` (argv + env + files in the sandbox's io dir) and
:meth:`collect` (turn exit code + output into a :class:`HarnessResult`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from ratchet.errors import ConfigError
from ratchet.models import HarnessResult, Tier

__all__ = [
    "DUMMY_KEY",
    "EXIT_OK",
    "EXIT_HARNESS",
    "EXIT_BUDGET",
    "HarnessAdapter",
    "SandboxedHarness",
    "HarnessConfig",
    "HostPaths",
    "SandboxPaths",
    "SandboxPlan",
    "get_adapter",
    "adapter_name",
    "same_adapter",
]

# The only API key a harness ever sees. The real key lives in the proxy sidecar.
DUMMY_KEY = "ratchet-dummy-key"

# Harness process exit codes (mirroring the worker exit codes where they overlap).
EXIT_OK = 0
EXIT_HARNESS = 5
EXIT_BUDGET = 6


@dataclass(frozen=True)
class HarnessConfig:
    """Everything ``configure`` receives, as one value object."""

    proxy_url: str  # e.g. http://127.0.0.1:8081/v1 (as seen by the harness)
    mock_tools_url: str  # e.g. http://127.0.0.1:8082
    skills_dir: Path
    workspace: Path
    frozen_time: datetime | None
    seed: int | None
    agent_tier: Tier
    run_id: str


@dataclass(frozen=True)
class HostPaths:
    """Directories on the worker's filesystem that back one sandboxed run."""

    work: Path
    skills: Path
    io: Path  # scratch for task files, results, HERMES_HOME; not part of the workspace diff


@dataclass(frozen=True)
class SandboxPaths:
    """The same directories as the harness inside the sandbox sees them."""

    work: str
    skills: str
    io: str


@dataclass(frozen=True)
class SandboxPlan:
    argv: list[str]
    env: dict[str, str] = field(default_factory=dict)  # non-secret, harness-specific


class HarnessAdapter(Protocol):
    """The adapter interface."""

    name: str

    def configure(
        self,
        *,
        proxy_url: str,
        mock_tools_url: str,
        skills_dir: Path,
        workspace: Path,
        frozen_time: datetime | None,
        seed: int | None,
        agent_tier: Tier,
        run_id: str,
    ) -> None: ...

    def run_task(self, task_input: str, *, timeout_s: int) -> HarnessResult: ...

    def loaded_skills(self) -> list[str]: ...

    def reload_skills(self) -> None: ...

    def pre_task_snapshot(self) -> Path: ...


@runtime_checkable
class SandboxedHarness(HarnessAdapter, Protocol):
    """An adapter the worker can run inside a :class:`ratchet.replay.sandbox.Sandbox`.

    ``configure`` must have been called with the URLs and paths *as seen inside the
    sandbox*; ``host`` gives the worker-side directories for writing files.
    """

    def plan(
        self,
        task_input: str,
        *,
        timeout_s: float,
        host: HostPaths,
        inside: SandboxPaths,
        python: str,
    ) -> SandboxPlan: ...

    def collect(
        self, exit_code: int, stdout: str, stderr: str, *, host: HostPaths
    ) -> HarnessResult: ...


def adapter_name(harness_version: str) -> str:
    """``"hermes/0.22.2"`` → ``"hermes"`` (the adapter part of ``EnvSnapshot.harness_version``)."""
    return harness_version.split("/", 1)[0].strip().lower()


def same_adapter(golden_version: str, replay_version: str) -> bool:
    """ADR 0003: a replay is only comparable with a golden run by the same adapter."""
    return adapter_name(golden_version) == adapter_name(replay_version)


def get_adapter(name: str) -> SandboxedHarness:
    """``RATCHET_HARNESS=hermes|minimal`` → a fresh adapter."""
    if name == "minimal":
        from ratchet.replay.harness.minimal import MinimalRunner

        return MinimalRunner()
    if name == "hermes":
        from ratchet.replay.harness.hermes import HermesAdapter

        return HermesAdapter()
    raise ConfigError(f"unknown harness {name!r}", code="config_error", context={"harness": name})
