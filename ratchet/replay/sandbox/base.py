"""The ``Sandbox`` protocol shared by the process, Docker and OpenShell sandboxes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ratchet.replay.harness.base import HostPaths, SandboxPaths

__all__ = ["Sandbox", "Endpoint", "TIMEOUT_EXIT"]

# Exit code a sandbox reports when it killed the command at its wall clock (as timeout(1)).
TIMEOUT_EXIT = 124


@dataclass(frozen=True)
class Endpoint:
    """A host:port the sandbox may reach (the proxy and mock-tool sidecars)."""

    host: str
    port: int


class Sandbox(Protocol):
    """One isolated run of a harness command.

    ``bind_host`` is where the worker must bind its sidecars so the sandbox can reach them;
    ``connect_host`` is how the harness inside addresses them. ``paths`` maps the worker's
    directories to the paths seen inside. ``create`` → ``run`` (once or more) → ``destroy``.
    """

    name: str
    bind_host: str
    connect_host: str
    python: str  # interpreter to run ``python -m ratchet...`` inside the sandbox
    faketime_lib: str | None  # libfaketime path inside the sandbox, if available

    def paths(self, host: HostPaths) -> SandboxPaths: ...

    def create(
        self,
        workdir: Path,
        skills_dir: Path,
        env: Mapping[str, str],
        *,
        io_dir: Path | None = None,
        egress: Sequence[Endpoint] = (),
    ) -> None: ...

    def run(self, argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]: ...

    def destroy(self) -> None: ...
