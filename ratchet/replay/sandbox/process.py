"""ProcessSandbox: the harness as a plain subprocess (development only, ADR 0017).

No egress enforcement: only the MinimalRunner, which talks solely to the loopback proxy
and mock tools, is supported here. The environment is built from scratch and scrubbed
(:func:`ratchet.replay.sandbox.env.scrub_env`), the working directory is the workspace,
and the sandbox refuses to run when ``RATCHET_PUBLIC_MODE`` is on.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import SandboxError
from ratchet.replay.harness.base import HostPaths, SandboxPaths
from ratchet.replay.sandbox.base import TIMEOUT_EXIT, Endpoint
from ratchet.replay.sandbox.env import scrub_env

__all__ = ["ProcessSandbox"]

_FAKETIME_CANDIDATES = (
    "/usr/local/lib/libfaketime.so.1",
    "/usr/lib/x86_64-linux-gnu/faketime/libfaketime.so.1",
    "/usr/lib/aarch64-linux-gnu/faketime/libfaketime.so.1",
)


def _find_faketime() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    return next((p for p in _FAKETIME_CANDIDATES if Path(p).is_file()), None)


class ProcessSandbox:
    name = "process"
    bind_host = "127.0.0.1"
    connect_host = "127.0.0.1"

    def __init__(self, settings: Settings | None = None, *, python: str | None = None) -> None:
        self._settings = settings or get_settings()
        self.python = python or sys.executable
        self.faketime_lib = _find_faketime()
        self._workdir: Path | None = None
        self._env: dict[str, str] | None = None
        self._proc: subprocess.Popen[str] | None = None

    def paths(self, host: HostPaths) -> SandboxPaths:
        return SandboxPaths(work=str(host.work), skills=str(host.skills), io=str(host.io))

    @property
    def env(self) -> dict[str, str]:
        """The exact environment the harness process receives."""
        if self._env is None:
            raise SandboxError("sandbox not created", code="sandbox_failed")
        return dict(self._env)

    def create(
        self,
        workdir: Path,
        skills_dir: Path,
        env: Mapping[str, str],
        *,
        io_dir: Path | None = None,
        egress: Sequence[Endpoint] = (),
    ) -> None:
        if self._settings.ratchet_public_mode:
            raise SandboxError(
                "the process sandbox is development-only and refused in public mode",
                code="sandbox_failed",
                context={"sandbox": self.name},
            )
        if not workdir.is_dir() or not skills_dir.is_dir():
            raise SandboxError("workspace or skills directory missing", code="sandbox_failed")
        home = io_dir or Path(tempfile.mkdtemp(prefix="ratchet-sbx-home-"))
        base = {
            "PATH": f"{Path(self.python).parent}:/usr/bin:/bin",
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "TZ": "UTC",
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self._env = scrub_env({**base, **env}, self._settings)
        self._workdir = workdir

    def run(self, argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]:
        if self._env is None or self._workdir is None:
            raise SandboxError("sandbox not created", code="sandbox_failed")
        try:
            self._proc = subprocess.Popen(
                list(argv),
                cwd=self._workdir,
                env=self._env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except OSError as exc:
            raise SandboxError(
                "could not start the harness process", code="sandbox_failed"
            ) from exc
        try:
            out, err = self._proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._kill()
            out, err = self._proc.communicate()
            return TIMEOUT_EXIT, out, err + "\n[ratchet] harness killed at the wall-clock cap\n"
        return self._proc.returncode, out, err

    def destroy(self) -> None:
        self._kill()
        self._env = None
        self._workdir = None

    def _kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
