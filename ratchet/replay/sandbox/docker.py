"""DockerSandbox: one locked-down container per replay (ADR 0010 fallback).

Each ``run`` is a ``docker run --rm`` of the worker image with:

- ``--network ratchet-sandbox`` — an *internal* bridge (no route off the host) created once
  with :func:`network_create_argv`::

      docker network create --driver bridge --internal \\
          --subnet 172.30.0.0/24 --gateway 172.30.0.1 \\
          --opt com.docker.network.bridge.enable_icc=false ratchet-sandbox

  The worker binds its proxy and mock-tool sidecars on the bridge gateway address
  (``172.30.0.1``), so the only reachable endpoints are those two sidecars; nothing on
  the network can reach the internet, and ICC is off so sandboxes cannot see each other.
- ``--read-only --cap-drop ALL --security-opt no-new-privileges --pids-limit --memory
  --cpus``, a non-root ``--user``, a ``noexec`` tmpfs on ``/tmp``, and no Docker socket.
- bind mounts ``/work`` (workspace, rw), ``/skills`` (ro) and ``/io`` (task/result, rw).
- an env built from scratch and scrubbed; frozen time via ``FAKETIME`` + the libfaketime
  preload installed in the image at :data:`FAKETIME_LIB`.

Measured (docs/research/docker-sandbox-s1.md): on a Linux host the ``--internal`` bridge gateway
is a host interface, so sidecars bound there are reachable from the sandbox and nothing else
is. On macOS (Docker Desktop / OrbStack) the bridge lives in a VM, the gateway is not a host
interface, and :meth:`DockerSandbox.preflight` raises :data:`LINUX_HOST_REQUIRED`; there, run
the worker in a container with ``--network host`` (the docker-runner compose profile) or use
``RATCHET_SANDBOX=process``. Docker-in-job on Nebius Serverless is still unverified.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import SandboxError
from ratchet.replay.harness.base import HostPaths, SandboxPaths
from ratchet.replay.sandbox.base import TIMEOUT_EXIT, Endpoint
from ratchet.replay.sandbox.env import scrub_env
from ratchet.util import new_id

__all__ = [
    "DockerOptions",
    "DockerSandbox",
    "FAKETIME_LIB",
    "LINUX_HOST_REQUIRED",
    "CommandRunner",
    "gateway_bindable",
    "network_create_argv",
    "run_argv",
    "run_command",
    "cli_env",
]

FAKETIME_LIB = "/usr/local/lib/libfaketime.so.1"
_INSIDE = SandboxPaths(work="/work", skills="/skills", io="/io")
_STARTUP_GRACE_S = 30.0

LINUX_HOST_REQUIRED = (
    "the docker sandbox needs a Linux host (the bridge gateway must be a host interface); "
    "on macOS use the docker-runner compose profile or RATCHET_SANDBOX=process for development"
)

# (argv, timeout_s) -> (exit_code, stdout, stderr); TIMEOUT_EXIT on timeout.
CommandRunner = Callable[[list[str], float | None], tuple[int, str, str]]


def cli_env() -> dict[str, str]:
    """Explicit environment for worker-side CLIs (docker, openshell); holds no secrets."""
    return {"PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(Path.home())}


def run_command(argv: list[str], timeout_s: float | None) -> tuple[int, str, str]:
    """Run a worker-side CLI (docker/openshell) with a minimal explicit environment."""
    try:
        proc = subprocess.run(
            argv,
            env=cli_env(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout if isinstance(exc.stdout, str) else ""
        return TIMEOUT_EXIT, out, "timeout"
    except FileNotFoundError as exc:
        raise SandboxError(f"{argv[0]} is not installed", code="sandbox_failed") from exc
    return proc.returncode, proc.stdout, proc.stderr


@dataclass(frozen=True)
class DockerOptions:
    image: str
    network: str = "ratchet-sandbox"
    subnet: str = "172.30.0.0/24"
    gateway: str = "172.30.0.1"
    memory: str = "2g"
    cpus: str = "2"
    pids_limit: int = 256
    user: str = "10001:10001"
    tmpfs_size: str = "256m"
    docker_bin: str = "docker"


def gateway_bindable(gateway: str) -> bool:
    """True if ``gateway`` is an address of this network namespace (sidecars can bind it)."""
    family = socket.AF_INET6 if ":" in gateway else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((gateway, 0))
        except OSError:
            return False
    return True


def gateway_hint(opts: DockerOptions) -> str:
    """Why the sidecars cannot bind the bridge gateway, and what to do instead."""
    if sys.platform != "linux":
        return LINUX_HOST_REQUIRED
    return (
        f"the {opts.network} bridge gateway {opts.gateway} is not an interface in this network "
        "namespace; run the worker with --network host (deploy/docker-compose.docker-runner.yml)"
    )


def network_create_argv(opts: DockerOptions) -> list[str]:
    return [
        opts.docker_bin,
        "network",
        "create",
        "--driver",
        "bridge",
        "--internal",
        "--subnet",
        opts.subnet,
        "--gateway",
        opts.gateway,
        "--opt",
        "com.docker.network.bridge.enable_icc=false",
        opts.network,
    ]


def run_argv(
    opts: DockerOptions,
    name: str,
    host: HostPaths,
    argv: Sequence[str],
    env: Mapping[str, str],
) -> list[str]:
    """The full ``docker run`` command for one harness invocation (pure)."""
    if not argv:
        raise SandboxError("empty harness command", code="sandbox_failed")
    cmd = [opts.docker_bin, "run", "--rm", "--name", name, "--network", opts.network]
    cmd += ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
    cmd += ["--pids-limit", str(opts.pids_limit), "--memory", opts.memory]
    cmd += ["--memory-swap", opts.memory, "--cpus", opts.cpus, "--user", opts.user]
    cmd += ["--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={opts.tmpfs_size}"]
    cmd += ["--mount", f"type=bind,src={host.work},dst={_INSIDE.work}"]
    cmd += ["--mount", f"type=bind,src={host.skills},dst={_INSIDE.skills},readonly"]
    cmd += ["--mount", f"type=bind,src={host.io},dst={_INSIDE.io}"]
    cmd += ["--workdir", _INSIDE.work]
    for key in sorted(env):
        cmd += ["--env", f"{key}={env[key]}"]
    cmd += ["--entrypoint", argv[0], opts.image, *argv[1:]]
    return cmd


class DockerSandbox:
    name = "docker"
    python = "python"
    faketime_lib: str | None = FAKETIME_LIB

    def __init__(
        self,
        options: DockerOptions | None = None,
        *,
        settings: Settings | None = None,
        runner: CommandRunner = run_command,
        bindable: Callable[[str], bool] = gateway_bindable,
    ) -> None:
        self._settings = settings or get_settings()
        self.options = options or DockerOptions(image=self._settings.ratchet_worker_image)
        self.bind_host = self.options.gateway
        self.connect_host = self.options.gateway
        self.bind_hint = gateway_hint(self.options)
        self._runner = runner
        self._bindable = bindable
        self._host: HostPaths | None = None
        self._env: dict[str, str] | None = None
        self._name = ""

    def paths(self, host: HostPaths) -> SandboxPaths:
        return _INSIDE

    def ensure_network(self) -> None:
        opts = self.options
        code, _, _ = self._runner([opts.docker_bin, "network", "inspect", opts.network], 30.0)
        if code == 0:
            return
        code, _, _ = self._runner(network_create_argv(opts), 30.0)
        if code != 0:
            raise SandboxError("could not create the sandbox network", code="sandbox_failed")

    def preflight(self) -> None:
        """Before the sidecars bind: the bridge exists and its gateway is a local address."""
        self.ensure_network()
        if not self._bindable(self.options.gateway):
            raise SandboxError(
                self.bind_hint,
                code="sandbox_failed",
                context={
                    "gateway": self.options.gateway,
                    "network": self.options.network,
                    "platform": sys.platform,
                },
            )

    def create(
        self,
        workdir: Path,
        skills_dir: Path,
        env: Mapping[str, str],
        *,
        io_dir: Path | None = None,
        egress: Sequence[Endpoint] = (),
    ) -> None:
        if io_dir is None:
            raise SandboxError("DockerSandbox needs an io dir", code="sandbox_failed")
        self.ensure_network()
        base = {"PATH": "/app/.venv/bin:/usr/local/bin:/usr/bin:/bin", "HOME": _INSIDE.io}
        base |= {"LANG": "C.UTF-8", "TZ": "UTC", "PYTHONUNBUFFERED": "1"}
        self._env = scrub_env({**base, **env}, self._settings)
        self._host = HostPaths(work=workdir, skills=skills_dir, io=io_dir)
        self._name = f"ratchet-sbx-{new_id('s')}"

    def command(self, argv: Sequence[str]) -> list[str]:
        if self._host is None or self._env is None:
            raise SandboxError("sandbox not created", code="sandbox_failed")
        return run_argv(self.options, self._name, self._host, argv, self._env)

    def run(self, argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]:
        code, out, err = self._runner(self.command(argv), timeout_s + _STARTUP_GRACE_S)
        if code == TIMEOUT_EXIT:
            self._runner([self.options.docker_bin, "kill", self._name], 30.0)
        return code, out, err

    def destroy(self) -> None:
        if self._name:
            self._runner([self.options.docker_bin, "rm", "-f", self._name], 30.0)
        self._host, self._env, self._name = None, None, ""
