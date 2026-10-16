"""HermesAdapter: run Hermes Agent (v0.22.x) one task at a time.

Design (docs/research/hermes-nemoclaw-openshell.md, "Recommended adapter design"), with the
fixes a live Hermes v0.22.2 run confirmed (docs/research/hermes-live-s3.md, C1–C10):

- **Isolated state per run.** ``HERMES_HOME=<dir>/hermes_home`` holds a generated
  ``config.yaml``, an empty ``.env`` and a copy of the candidate skills under ``skills/``
  (:mod:`ratchet.replay.harness.hermes_config`), so a run never touches ``~/.hermes``.
  ``HERMES_BUNDLED_SKILLS`` points at an empty directory, so the model sees only the
  candidate skills (C5).
- **Model endpoint.** A named provider ``ratchet`` with the proxy's *path form*
  ``<proxy>/r/<run_id>`` (attribution without headers), the dummy key and the
  ``X-Ratchet-*`` headers (C2). Hermes sends no ``temperature``/``seed``; the proxy injects
  the run's seed.
- **Tools.** The mock tool server is the MCP server ``ratchet_mock`` at ``<mock>/mcp`` with
  the run-id header; its tools reach the model as ``mcp__ratchet_mock__<tool>`` (C1). The
  toolsets are an allowlist (``ENABLED_TOOLSETS``: the mock MCP server, ``file`` and
  ``skills``) passed with ``--toolsets`` and ``platform_toolsets.cli``; every other known
  toolset is in ``agent.disabled_toolsets`` too. Hermes needs a real sandbox (Docker or
  OpenShell): ``harness=hermes`` with ``sandbox=process`` is refused (``sandbox_refusal``).
- **Run.** ``hermes chat --oneshot -Q -q <task> --format stream-json --run-budget <s>``
  (``--usage-file`` is a top-level flag that one-shot chat ignores, C4) → JSONL events on
  stdout, parsed by :func:`parse_stream`. ``--run-budget`` (90 % of the timeout) and
  ``agent.api_max_retries: 1`` turn an unreachable proxy into a clean ``result.error``
  instead of a 5-minute back-off (C9).
- **Stable prompt (C10).** Hermes writes ``HOME``, the cwd, ``HERMES_HOME/profiles`` and its
  scratch dir into the system prompt. :meth:`HermesAdapter.run_task` therefore runs every
  task in the same ``<home_root>/current`` directory (wiped per run, one run at a time per
  root), and the outputs are kept in ``<home_root>/<run_id>/``; :meth:`HermesAdapter.plan`
  uses the sandbox's fixed io path. Same skills + task → same request hash across runs.
- **Reload.** ``reload_skills()`` is a no-op: each one-shot is a fresh process that copies
  ``skills_dir`` and builds its skills index at session start (confirmed live).
- **Version (C8).** No event carries it: ``harness_version`` is ``hermes/<version>`` from the
  version baked in at install time (``RATCHET_HERMES_VERSION``) or one ``hermes --version``
  per process (:func:`ratchet.replay.harness.hermes_stream.hermes_version`).
- **Time.** Hermes reads the system clock and prints ``Conversation started: <date>``;
  frozen time comes from ``libfaketime`` (``FAKETIME`` + ``LD_PRELOAD``) when the library is
  available (Linux sandbox only; still unverified with Hermes, see below).

Still unverified: ``skills.creation_nudge_interval: 0`` on long runs, and
libfaketime with Hermes's Python 3.14.
"""

from __future__ import annotations

import contextlib
import fcntl
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from ratchet.errors import HarnessError
from ratchet.models import HarnessResult, Tier
from ratchet.replay.harness.base import HarnessConfig, HostPaths, SandboxPaths, SandboxPlan
from ratchet.replay.harness.hermes_config import (
    DISABLED_TOOLSETS,
    ENABLED_TOOLSETS,
    MAX_TURNS,
    MCP_SERVER,
    MCP_TOOL_PREFIX,
    NO_BUNDLED_SKILLS,
    PROVIDER,
    build_config,
    write_home,
)
from ratchet.replay.harness.hermes_stream import HermesRun, hermes_version, parse_stream
from ratchet.replay.harness.skills import load_skills
from ratchet.replay.sandbox.env import faketime_env
from ratchet.store.archive import pack_dir

__all__ = [
    "HermesAdapter",
    "HermesRun",
    "MCP_SERVER",
    "MCP_TOOL_PREFIX",
    "PROVIDER",
    "ENABLED_TOOLSETS",
    "DISABLED_TOOLSETS",
    "RUN_BUDGET_FRACTION",
    "build_config",
    "parse_stream",
    "write_home",
]

RUN_BUDGET_FRACTION = 0.9  # --run-budget: finish cleanly before the subprocess timeout
_PATH = "/usr/local/bin:/usr/bin:/bin"
_CURRENT = "current"  # the run-independent per-root run directory (C10)


@contextlib.contextmanager
def _exclusive(lock_file: Path) -> Iterator[None]:
    """One run at a time per home root (threads and processes)."""
    with lock_file.open("a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _baked_version() -> str | None:
    """``RATCHET_HERMES_VERSION`` if the settings model carries it (set at install time)."""
    from ratchet.config.settings import get_settings

    value = getattr(get_settings(), "ratchet_hermes_version", None)
    return value if isinstance(value, str) and value else None


class HermesAdapter:
    name = "hermes"

    def __init__(
        self,
        *,
        hermes_bin: str = "hermes",
        home_root: Path | None = None,
        faketime_lib: str | None = None,
        max_turns: int = MAX_TURNS,
        version: str | None = None,
    ) -> None:
        self._bin = hermes_bin
        self._home_root = home_root
        self._faketime_lib = faketime_lib
        self._max_turns = max_turns
        self._version = version
        self._cfg: HarnessConfig | None = None

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
    ) -> None:
        self._cfg = HarnessConfig(
            proxy_url, mock_tools_url, skills_dir, workspace, frozen_time, seed, agent_tier, run_id
        )

    @property
    def config(self) -> HarnessConfig:
        if self._cfg is None:
            raise HarnessError("HermesAdapter.configure() was not called", code="harness_failed")
        return self._cfg

    def loaded_skills(self) -> list[str]:
        return [skill.name for skill in load_skills(self.config.skills_dir)]

    def reload_skills(self) -> None:
        """No-op: every ``--oneshot`` run is a fresh process with a fresh skills index."""

    def pre_task_snapshot(self) -> Path:
        dest = self._root() / f"{self.config.run_id}.workspace.tar.zst"
        return pack_dir(self.config.workspace, dest)

    def version(self) -> str:
        """The Hermes version (never probed more than once per process and binary)."""
        baked = self._version or _baked_version()
        return hermes_version(self._resolved_bin(), baked)

    def argv(self, task_input: str, *, timeout_s: float) -> list[str]:
        budget = max(1, int(float(timeout_s) * RUN_BUDGET_FRACTION))
        return [
            self._bin,
            "chat",
            "--oneshot",
            "-Q",
            "-q",
            task_input,
            "--format",
            "stream-json",
            "--max-turns",
            str(self._max_turns),
            "--toolsets",
            ",".join(ENABLED_TOOLSETS),
            "--run-budget",
            str(budget),
        ]

    def env(self, run_dir: str) -> dict[str, str]:
        """Env for a run whose ``HOME`` is ``run_dir`` (home at ``<run_dir>/hermes_home``)."""
        return {
            "HERMES_HOME": f"{run_dir}/hermes_home",
            "HERMES_BUNDLED_SKILLS": f"{run_dir}/{NO_BUNDLED_SKILLS}",
            "HERMES_TIMEZONE": "UTC",
            "HOME": run_dir,
            "TZ": "UTC",
        }

    def run_task(self, task_input: str, *, timeout_s: int | float) -> HarnessResult:
        """Run Hermes directly (control-plane demo agent; no sandbox)."""
        cfg = self.config
        root = self._root()
        out = root / cfg.run_id
        out.mkdir(parents=True, exist_ok=True)
        argv = self.argv(task_input, timeout_s=timeout_s)
        argv[0] = self._resolved_bin()  # the child's PATH is minimal
        with _exclusive(root / ".lock"):
            current = root / _CURRENT
            if current.exists():
                shutil.rmtree(current)
            write_home(current / "hermes_home", cfg, cfg.skills_dir)
            env = self.env(str(current))
            env.update(faketime_env(cfg.frozen_time, self._faketime_lib))
            env["PATH"] = _PATH
            code, stdout, stderr = self._spawn(argv, env, cfg.workspace, timeout_s)
        (out / "stdout.jsonl").write_text(stdout, encoding="utf-8")
        (out / "stderr.txt").write_text(stderr, encoding="utf-8")
        host = HostPaths(work=cfg.workspace, skills=cfg.skills_dir, io=out)
        result = self.collect(code, stdout, stderr, host=host)
        update = {"stdout_ref": str(out / "stdout.jsonl"), "stderr_ref": str(out / "stderr.txt")}
        return result.model_copy(update=update)

    def _spawn(
        self, argv: list[str], env: dict[str, str], cwd: Path, timeout_s: float
    ) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(
                argv,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=float(timeout_s),
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"
        except FileNotFoundError as exc:
            raise HarnessError("hermes is not installed", code="harness_failed") from exc
        return proc.returncode, proc.stdout, proc.stderr

    def plan(
        self,
        task_input: str,
        *,
        timeout_s: float,
        host: HostPaths,
        inside: SandboxPaths,
        python: str,
    ) -> SandboxPlan:
        write_home(host.io / "hermes_home", self.config, host.skills)
        argv = self.argv(task_input, timeout_s=timeout_s)
        return SandboxPlan(argv=argv, env=self.env(inside.io))

    def collect(
        self, exit_code: int, stdout: str, stderr: str, *, host: HostPaths
    ) -> HarnessResult:
        run = parse_stream(stdout)
        ok = exit_code == 0 and run.exit_code in (None, 0) and run.error is None
        return HarnessResult(
            final_output=run.final_text,
            exit_ok=ok,
            stdout_ref="",
            stderr_ref="",
            skills_invoked=run.skills_viewed,
            harness_version=f"hermes/{run.version or self.version()}",
        )

    def _resolved_bin(self) -> str:
        """Absolute path: the child runs with a minimal PATH and the workspace as cwd."""
        found = shutil.which(self._bin)
        return str(Path(found).absolute()) if found else self._bin

    def _root(self) -> Path:
        if self._home_root is None:
            # Fixed, not mkdtemp: the path reaches the prompt (C10).
            self._home_root = Path(tempfile.gettempdir()) / "ratchet-hermes"
        self._home_root.mkdir(parents=True, exist_ok=True)
        return self._home_root
