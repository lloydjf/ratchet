"""LocalJobRunner: run a batch on this machine (the fallback, and the VM demo path).

``mode="process"``: ``sys.executable -m ratchet.replay.worker --inputs … --output …`` as a
child process. ``mode="docker"``: the ``ratchet-worker`` image with the inputs mounted
read-only at ``/in`` and the output at ``/out``. Either way progress comes from the
worker's ``progress.json`` and the exit code from the process / container (falling back to
``exit.json``), and ``submit`` is idempotent by ``batch_id``.

The worker process is trusted (it hosts the proxy sidecar), so its environment carries the
settings — secrets included — built explicitly by :func:`settings_env`; in docker mode they
travel in a 0600 ``--env-file`` that is deleted once the container exists. The worker's
*sandbox* never sees them.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import NotFoundError, SandboxError
from ratchet.models import ReplayBatch
from ratchet.replay.jobs.base import JobState, JobStatus, parse_progress, read_exit_code
from ratchet.replay.sandbox.docker import CommandRunner, run_command
from ratchet.util import copy_tree_contents

__all__ = ["LocalJobRunner", "settings_env", "docker_worker_argv"]

_JOB_PREFIX = "local-"


def settings_env(settings: Settings) -> dict[str, str]:
    """Settings → ``NAME=value`` env for a trusted child process (paths made absolute)."""
    env: dict[str, str] = {}
    for name in type(settings).model_fields:
        value = getattr(settings, name)
        if value is None:
            continue
        if isinstance(value, SecretStr):
            text = value.get_secret_value()
        elif isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, dict | list):
            text = json.dumps(value)
        elif isinstance(value, Path):
            text = str(value.resolve())
        else:
            text = str(value)
        env[name.upper()] = text
    return env


def docker_worker_argv(
    settings: Settings, spec: ReplayBatch, inputs: Path, output: Path, env_file: Path
) -> list[str]:
    """``docker run -d`` for one worker container (pure)."""
    cmd = ["docker", "run", "-d", "--name", f"ratchet-worker-{spec.batch_id.replace('_', '-')}"]
    cmd += ["--mount", f"type=bind,src={inputs.resolve()},dst=/in,readonly"]
    cmd += ["--mount", f"type=bind,src={output.resolve()},dst=/out"]
    cmd += ["--env-file", str(env_file)]
    if spec.sandbox == "docker":
        # The inner DockerSandbox needs the daemon and the internal bridge's gateway address
        # on the worker's network namespace (unverified); the sandbox itself gets neither.
        cmd += ["--mount", "type=bind,src=/var/run/docker.sock,dst=/var/run/docker.sock"]
        cmd += ["--network", "host"]
    return [*cmd, spec.worker_image, "--inputs", "/in", "--output", "/out"]


class LocalJobRunner:
    def __init__(
        self,
        mode: Literal["process", "docker"] = "process",
        *,
        settings: Settings | None = None,
        work_root: Path | None = None,
        worker_args: Sequence[str] = (),
        python: str | None = None,
        runner: CommandRunner = run_command,
    ) -> None:
        self.mode = mode
        self.name = f"local-{mode}"
        self._settings = settings or get_settings()
        self._root = work_root or Path(tempfile.mkdtemp(prefix="ratchet-jobs-"))
        self._worker_args = list(worker_args)
        self._python = python or sys.executable
        self._runner = runner
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._sizes: dict[str, int] = {}

    def output_dir(self, job_id: str) -> Path:
        if not job_id.startswith(_JOB_PREFIX) or "/" in job_id or ".." in job_id:
            raise NotFoundError(f"unknown job {job_id}", code="not_found")
        return self._root / job_id[len(_JOB_PREFIX) :] / "out"

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        job_id = f"{_JOB_PREFIX}{spec.batch_id}"
        out = self.output_dir(job_id)
        self._sizes[job_id] = len(spec.golden_run_ids)
        if job_id in self._procs or (out / "exit.json").exists() or self._container_known(job_id):
            return job_id
        if self.mode == "process" and self._orphan_pid(job_id) is not None:
            return job_id  # adopted after a control-plane restart; never run a batch twice
        out.mkdir(parents=True, exist_ok=True)
        if self.mode == "process":
            self._start_process(job_id, inputs_dir, out)
        else:
            await asyncio.to_thread(self._start_container, spec, inputs_dir, out)
        return job_id

    def _start_process(self, job_id: str, inputs_dir: Path, out: Path) -> None:
        env = settings_env(self._settings)
        env |= {"PATH": f"{Path(self._python).parent}:/usr/local/bin:/usr/bin:/bin"}
        env |= {"HOME": str(Path.home()), "PYTHONUNBUFFERED": "1"}
        argv = [self._python, "-m", "ratchet.replay.worker", "--inputs", str(inputs_dir.resolve())]
        argv += ["--output", str(out.resolve()), *self._worker_args]
        with (out.parent / "worker.stdout.log").open("ab") as log:
            proc = subprocess.Popen(
                argv, cwd=out.parent, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log
            )
        self._procs[job_id] = proc
        (out.parent / "worker.pid").write_text(str(proc.pid), encoding="utf-8")

    def _orphan_pid(self, job_id: str) -> int | None:
        """The pid of a worker this runner did not start (an earlier control-plane process
        did) that is still running for this job, matched on pid *and* command line so a
        recycled pid is never mistaken for it."""
        pid_file = self.output_dir(job_id).parent / "worker.pid"
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None
        code, text, _ = self._runner(["ps", "-o", "command=", "-p", str(pid)], 10.0)
        out = str(self.output_dir(job_id).resolve())
        return pid if code == 0 and "ratchet.replay.worker" in text and out in text else None

    def _start_container(self, spec: ReplayBatch, inputs_dir: Path, out: Path) -> None:
        fd, name = tempfile.mkstemp(prefix="worker-", suffix=".env", dir=out.parent)
        env_file = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                for key, value in sorted(settings_env(self._settings).items()):
                    fh.write(f"{key}={value}\n")
            argv = docker_worker_argv(self._settings, spec, inputs_dir, out, env_file)
            code, _, _ = self._runner(argv, 120.0)
        finally:
            env_file.unlink(missing_ok=True)
        if code != 0:
            raise SandboxError("could not start the worker container", code="sandbox_failed")

    def _container(self, job_id: str) -> str:
        return f"ratchet-worker-{job_id[len(_JOB_PREFIX) :].replace('_', '-')}"

    def _container_known(self, job_id: str) -> bool:
        if self.mode != "docker":
            return False
        code, _, _ = self._runner(["docker", "inspect", self._container(job_id)], 30.0)
        return code == 0

    def _state(self, job_id: str, out: Path) -> tuple[JobState, int | None]:
        if self.mode == "process":
            proc = self._procs.get(job_id)
            if proc is not None and proc.poll() is None:
                return "running", None
            if proc is None and self._orphan_pid(job_id) is not None:
                return "running", None
            code = proc.returncode if proc is not None else read_exit_code(out / "exit.json")
        else:
            fmt = "{{.State.Status}} {{.State.ExitCode}}"
            rc, text, _ = self._runner(
                ["docker", "inspect", "-f", fmt, self._container(job_id)], 30
            )
            status, _, raw_code = text.strip().partition(" ")
            if rc == 0 and status in {"created", "running", "restarting"}:
                return ("pending" if status == "created" else "running"), None
            code = int(raw_code) if rc == 0 and raw_code.lstrip("-").isdigit() else None
            code = code if code is not None else read_exit_code(out / "exit.json")
        if code is None:
            return "failed", None  # lost: no process, no container, no exit marker
        return ("succeeded" if code == 0 else "failed"), code

    async def poll(self, job_id: str) -> JobStatus:
        out = self.output_dir(job_id)
        state, code = await asyncio.to_thread(self._state, job_id, out)
        progress = out / "progress.json"
        raw = progress.read_bytes() if progress.is_file() else None
        k, n, beat = parse_progress(raw)
        return JobStatus(state, k, n or self._sizes.get(job_id, 0), beat, code)

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        out = self.output_dir(job_id)
        if not out.is_dir():
            raise NotFoundError(f"no output for job {job_id}", code="not_found")
        if out.resolve() != output_dir.resolve():
            await asyncio.to_thread(copy_tree_contents, out, output_dir)
        if self.mode == "docker":
            await asyncio.to_thread(self._runner, ["docker", "rm", self._container(job_id)], 30.0)
        return output_dir

    async def cancel(self, job_id: str) -> None:
        if self.mode == "docker":
            argv = ["docker", "stop", "-t", "10", self._container(job_id)]
            await asyncio.to_thread(self._runner, argv, 60.0)
            return
        proc = self._procs.get(job_id)
        if proc is None:
            pid = self._orphan_pid(job_id)
            if pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGTERM)  # checkpoints a partial result, as below
            return
        if proc.poll() is not None:
            return
        proc.terminate()  # the worker checkpoints a partial result on SIGTERM
        try:
            await asyncio.to_thread(proc.wait, 15.0)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
