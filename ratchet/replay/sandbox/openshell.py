"""OpenShellSandbox: NVIDIA OpenShell with a deny-all policy (ADR 0010 primary path).

Lifecycle (docs/research/hermes-nemoclaw-openshell.md §C, CLI CONFIRMED):

- ``openshell sandbox create --name N --policy <generated yaml> --from <image> --detach
  --upload <work>:/sandbox/work --upload <skills>:/sandbox/skills --upload <io>:/sandbox/io
  --env K=V … -- sleep infinity``
- ``openshell sandbox exec -n N --timeout T -- <argv>``; afterwards ``work/`` and ``io/``
  are copied back with ``exec -- tar -C /sandbox -cf - work io`` so the worker can diff the
  workspace and read the harness result;
- ``openshell sandbox delete N`` on teardown.

The policy denies all egress except the proxy and mock-tool sidecars (``protocol: rest``,
``enforcement: enforce``) for the allowlisted interpreters, makes the skills read-only and
only ``/sandbox/work``, ``/sandbox/io`` and ``/tmp`` writable, and requires Landlock.

Still unverified: (1) the sandbox-side alias ``host.openshell.internal`` reaches sidecars bound on
the worker (bind host may need to be the runtime bridge address instead of loopback);
(2) plain-HTTP endpoints on non-443 ports are allowed by ``network_policies``; (3)
``--upload src:dst`` copies a directory's contents; (4) ``exec`` propagates the exit code
and returns binary stdout intact; (5) ``process.run_as_user`` value ``sandbox`` exists in
the image; (6) OpenShell runs inside a Nebius Serverless Job at all (Landlock ABI 3).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import SandboxError
from ratchet.replay.harness.base import HostPaths, SandboxPaths
from ratchet.replay.sandbox.base import Endpoint
from ratchet.replay.sandbox.docker import FAKETIME_LIB, CommandRunner, cli_env, run_command
from ratchet.replay.sandbox.env import scrub_env
from ratchet.util import new_id

__all__ = ["OpenShellOptions", "OpenShellSandbox", "policy", "create_argv", "exec_argv"]


@dataclass(frozen=True)
class OpenShellOptions:
    image: str
    openshell_bin: str = "openshell"
    host_alias: str = "host.openshell.internal"
    bind_host: str = "127.0.0.1"
    root: str = "/sandbox"
    binaries: tuple[str, ...] = ("/app/.venv/bin/python*", "/usr/local/bin/python3*")
    run_as: str = "sandbox"


def _inside(opts: OpenShellOptions) -> SandboxPaths:
    return SandboxPaths(
        work=f"{opts.root}/work", skills=f"{opts.root}/skills", io=f"{opts.root}/io"
    )


def policy(opts: OpenShellOptions, egress: Sequence[Endpoint]) -> dict[str, object]:
    """OpenShell policy (``version: 1``): deny all, allow only the sidecars (pure)."""
    inside = _inside(opts)
    rules: dict[str, object] = {}
    for index, endpoint in enumerate(egress):
        rules[f"ratchet_sidecar_{index}"] = {
            "endpoints": [
                {
                    "host": endpoint.host,
                    "port": endpoint.port,
                    "protocol": "rest",
                    "enforcement": "enforce",
                    "access": "full",
                }
            ],
            "binaries": [{"path": path} for path in opts.binaries],
        }
    return {
        "version": 1,
        "filesystem_policy": {
            "include_workdir": False,
            "read_only": ["/usr", "/lib", "/bin", "/etc", "/app", inside.skills],
            "read_write": [inside.work, inside.io, "/tmp"],
        },
        "landlock": {"compatibility": "hard_requirement"},
        "process": {"run_as_user": opts.run_as, "run_as_group": opts.run_as},
        "network_policies": rules,
    }


def create_argv(
    opts: OpenShellOptions,
    name: str,
    policy_path: Path,
    host: HostPaths,
    env: Mapping[str, str],
) -> list[str]:
    inside = _inside(opts)
    cmd = [opts.openshell_bin, "sandbox", "create", "--name", name]
    cmd += ["--policy", str(policy_path), "--from", opts.image, "--detach"]
    cmd += ["--upload", f"{host.work}:{inside.work}", "--upload", f"{host.skills}:{inside.skills}"]
    cmd += ["--upload", f"{host.io}:{inside.io}"]
    for key in sorted(env):
        cmd += ["--env", f"{key}={env[key]}"]
    return [*cmd, "--", "sleep", "infinity"]


def exec_argv(
    opts: OpenShellOptions, name: str, argv: Sequence[str], timeout_s: float
) -> list[str]:
    timeout = str(max(int(timeout_s), 1))
    return [opts.openshell_bin, "sandbox", "exec", "-n", name, "--timeout", timeout, "--", *argv]


def _extract(data: bytes, targets: dict[str, Path]) -> None:
    """Safely extract ``work/…`` and ``io/…`` members into their host directories."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        for member in tar.getmembers():
            parts = Path(member.name.lstrip("./")).parts
            if not parts or parts[0] not in targets or not (member.isfile() or member.isdir()):
                continue
            root = targets[parts[0]].resolve()
            dest = (root / Path(*parts[1:])).resolve() if len(parts) > 1 else root
            if not dest.is_relative_to(root):
                raise SandboxError(
                    "sandbox returned a path outside its tree", code="sandbox_failed"
                )
            if member.isdir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            fh = tar.extractfile(member)
            if fh is not None:
                dest.write_bytes(fh.read())


class OpenShellSandbox:
    name = "openshell"
    python = "python"
    faketime_lib: str | None = FAKETIME_LIB

    def __init__(
        self,
        options: OpenShellOptions | None = None,
        *,
        settings: Settings | None = None,
        runner: CommandRunner = run_command,
    ) -> None:
        self._settings = settings or get_settings()
        self.options = options or OpenShellOptions(image=self._settings.ratchet_worker_image)
        self.bind_host = self.options.bind_host
        self.connect_host = self.options.host_alias
        self._runner = runner
        self._host: HostPaths | None = None
        self._name = ""

    def paths(self, host: HostPaths) -> SandboxPaths:
        return _inside(self.options)

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
            raise SandboxError("OpenShellSandbox needs an io dir", code="sandbox_failed")
        clean = scrub_env({"HOME": _inside(self.options).io, "TZ": "UTC", **env}, self._settings)
        allowed = [Endpoint(self.options.host_alias, e.port) for e in egress]
        policy_path = io_dir.parent / "openshell-policy.yaml"
        policy_path.write_text(yaml.safe_dump(policy(self.options, allowed), sort_keys=False))
        self._host = HostPaths(work=workdir, skills=skills_dir, io=io_dir)
        self._name = f"ratchet-{new_id('s').replace('_', '-')}"
        cmd = create_argv(self.options, self._name, policy_path, self._host, clean)
        code, _, _ = self._runner(cmd, 120.0)
        if code != 0:
            self._name = ""
            raise SandboxError("openshell sandbox create failed", code="sandbox_failed")

    def run(self, argv: Sequence[str], timeout_s: float) -> tuple[int, str, str]:
        if self._host is None or not self._name:
            raise SandboxError("sandbox not created", code="sandbox_failed")
        cmd = exec_argv(self.options, self._name, argv, timeout_s)
        code, out, err = self._runner(cmd, timeout_s + 30.0)
        self._sync_back()
        return code, out, err

    def _sync_back(self) -> None:
        assert self._host is not None
        cmd = [self.options.openshell_bin, "sandbox", "exec", "-n", self._name, "--"]
        cmd += ["tar", "-C", self.options.root, "-cf", "-", "work", "io"]
        try:
            proc = subprocess.run(
                cmd, env=cli_env(), capture_output=True, timeout=120.0, check=False
            )
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            raise SandboxError("could not copy the workspace back", code="sandbox_failed") from exc
        if proc.returncode != 0:
            raise SandboxError("could not copy the workspace back", code="sandbox_failed")
        for child in self._host.work.iterdir():
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        _extract(proc.stdout, {"work": self._host.work, "io": self._host.io})

    def destroy(self) -> None:
        if self._name:
            self._runner([self.options.openshell_bin, "sandbox", "delete", self._name], 60.0)
        self._host, self._name = None, ""
