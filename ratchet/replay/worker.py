"""The replay worker: one batch in, ``result.json`` out (the worker contract).

Inputs (``inputs_dir``): ``spec.json`` (ReplayBatch), ``goldens/<run_id>.json``,
``cassettes.tar.zst``, ``skills.tar.zst``, ``workspaces/<run_id>.tar.zst`` and an optional
world overlay (``world.tar.zst`` or a ``world/`` directory) served before the cassettes.

Outputs (``output_dir``): ``result.json`` (ReplayResult, with ``unexpected_by_run`` and
``failed_goldens``), ``replays/<run_id>/`` trace directories, ``progress.json``
(``{"k","n","heartbeat_at"}``, after every replay and at least every 15 s),
``exit.json`` (``{"exit_code"}``, read by runners whose platform reports no exit code),
``worker.log`` and a copy of ``spec.json``.

Goldens run sequentially; each failure is isolated to its golden. ``result.json`` is
checkpointed after every golden with the not-yet-run goldens marked ``worker_lost``, so a
worker killed mid-batch still leaves a ``partial`` result. Exit codes:
0 ok, 3 partial, 4 sandbox failure (incl. unusable inputs), 5 harness failure, 6 budget.

The real Token Factory key is only in this process's settings (read by the in-process
proxy sidecar); the sandbox environment is built from scratch and scrubbed.
"""

from __future__ import annotations

import argparse
import shutil
import signal
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType

from pydantic import ValidationError

from ratchet import clock
from ratchet.config.settings import Settings, get_settings
from ratchet.errors import SandboxError
from ratchet.log import Logger, bind, get_logger
from ratchet.models import ReplayBatch, ReplayResult, Trace
from ratchet.replay.deps import WorkerDeps, default_deps, load_deps
from ratchet.replay.replay_one import BatchContext, GoldenOutcome, replay_golden
from ratchet.store import Store
from ratchet.store.archive import unpack
from ratchet.store.base import write_json_direct

__all__ = [
    "EXIT_OK",
    "EXIT_PARTIAL",
    "EXIT_SANDBOX",
    "EXIT_HARNESS",
    "EXIT_BUDGET",
    "WorkerTerminated",
    "main",
    "build_result",
    "exit_code_for",
    "cli",
]

EXIT_OK, EXIT_PARTIAL, EXIT_SANDBOX, EXIT_HARNESS, EXIT_BUDGET = 0, 3, 4, 5, 6
HEARTBEAT_S = 15.0
_INFRA = frozenset({"sandbox_failed", "worker_lost"})


class WorkerTerminated(BaseException):
    """SIGTERM (cancel): finish the current teardown, checkpoint, exit."""


class _Heartbeat:
    def __init__(self, path: Path, n: int, interval_s: float) -> None:
        self.path, self.n, self.k = path, n, 0
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="ratchet-heartbeat", daemon=True)

    def beat(self, k: int | None = None) -> None:
        if k is not None:
            self.k = k
        payload = {"k": self.k, "n": self.n, "heartbeat_at": clock.iso(clock.now())}
        write_json_direct(self.path, payload)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            self.beat()

    def __enter__(self) -> _Heartbeat:
        self.beat()
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5.0)


def build_result(
    spec: ReplayBatch, outcomes: dict[str, GoldenOutcome], *, lost_code: str = "worker_lost"
) -> ReplayResult:
    """ReplayResult from the outcomes so far; goldens without one are marked ``lost_code``."""
    replays: list[Trace] = []
    failed: dict[str, str] = {}
    by_run = {}
    for golden in spec.golden_run_ids:
        outcome = outcomes.get(golden)
        if outcome is None:
            failed[golden] = lost_code
            continue
        if outcome.error_code:
            failed[golden] = outcome.error_code
        if outcome.trace is not None:
            replays.append(outcome.trace)
            by_run[outcome.trace.run_id] = outcome.unexpected
    succeeded = len(spec.golden_run_ids) - len(failed)
    status = "ok" if not failed else ("partial" if succeeded > 0 else "failed")
    return ReplayResult(
        batch_id=spec.batch_id,
        replays=replays,
        unexpected_side_effects=[call for calls in by_run.values() for call in calls],
        worker_log_ref="worker.log",
        status=status,
        unexpected_by_run=by_run,
        failed_goldens=failed,
    )


def exit_code_for(result: ReplayResult) -> int:
    codes = set(result.failed_goldens.values())
    if "budget_exceeded" in codes:
        return EXIT_BUDGET
    if result.status == "ok":
        return EXIT_OK
    if result.status == "partial":
        return EXIT_PARTIAL
    return EXIT_SANDBOX if codes <= _INFRA else EXIT_HARNESS


class _WorkerLog:
    """Structured events to stdout and to ``worker.log`` (ids and codes only, no content)."""

    def __init__(self, path: Path) -> None:
        self._fh = path.open("a", encoding="utf-8")
        self._loggers = [get_logger("replay.worker"), Logger("replay.worker", stream=self._fh)]

    def info(self, event: str, **fields: object) -> None:
        for logger in self._loggers:
            logger.info(event, **fields)

    def warning(self, event: str, **fields: object) -> None:
        for logger in self._loggers:
            logger.warning(event, **fields)

    def close(self) -> None:
        self._fh.close()


def _load_spec(path: Path) -> ReplayBatch:
    return ReplayBatch.model_validate_json(path.read_text(encoding="utf-8"))


def _load_golden(inputs: Path, run_id: str) -> Trace | None:
    try:
        return Trace.model_validate_json((inputs / "goldens" / f"{run_id}.json").read_text())
    except (OSError, ValidationError, ValueError):
        return None


def _overlay(inputs: Path, scratch: Path) -> Path | None:
    tarball, directory = inputs / "world.tar.zst", inputs / "world"
    if tarball.is_file():
        unpack(tarball, scratch / "world")
        return scratch / "world"
    return directory if directory.is_dir() else None


def _context(
    spec: ReplayBatch, inputs: Path, output: Path, scratch: Path, s: Settings, deps: WorkerDeps
) -> BatchContext:
    skills_tar, cassettes_tar = inputs / "skills.tar.zst", inputs / "cassettes.tar.zst"
    if not skills_tar.is_file() or not cassettes_tar.is_file():
        raise SandboxError("batch inputs incomplete (skills or cassettes)", code="sandbox_failed")
    try:
        unpack(cassettes_tar, scratch / "cassettes")
        overlay = _overlay(inputs, scratch)
    except (OSError, ValueError) as exc:
        raise SandboxError("batch inputs unreadable", code="sandbox_failed") from exc
    store = Store.open(scratch / "store")
    return BatchContext(
        spec, inputs, output, scratch, store, s, deps, scratch / "cassettes", skills_tar, overlay
    )


def _finish(output: Path, result: ReplayResult, log: _WorkerLog) -> int:
    code = exit_code_for(result)
    write_json_direct(output / "result.json", result)
    write_json_direct(output / "exit.json", {"exit_code": code})
    log.info("replay.batch.done", status=result.status, exit_code=code, n=len(result.replays))
    return code


def _run_batch(
    ctx: BatchContext, outcomes: dict[str, GoldenOutcome], beat: _Heartbeat, log: _WorkerLog
) -> None:
    """Fill ``outcomes`` in place, so a SIGTERM mid-batch keeps what finished."""
    budget_hit = False
    for k, golden_id in enumerate(ctx.spec.golden_run_ids, start=1):
        golden = None if budget_hit else _load_golden(ctx.inputs, golden_id)
        if budget_hit:
            outcome = GoldenOutcome(golden_id, None, [], "budget_exceeded")
        elif golden is None:
            outcome = GoldenOutcome(golden_id, None, [], "sandbox_failed")
        else:
            outcome = replay_golden(ctx, golden)
        budget_hit = budget_hit or outcome.error_code == "budget_exceeded"
        outcomes[golden_id] = outcome
        log.info("replay.golden.outcome", k=k, error_code=outcome.error_code)
        write_json_direct(ctx.output / "result.json", build_result(ctx.spec, outcomes))
        beat.beat(k)


@contextmanager
def _scratch() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp(prefix="ratchet-worker-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def main(
    inputs_dir: Path,
    output_dir: Path,
    *,
    deps: WorkerDeps | None = None,
    settings: Settings | None = None,
    spec_path: Path | None = None,
    heartbeat_s: float = HEARTBEAT_S,
) -> int:
    """Run one batch; returns the worker exit code. ``result.json`` is always written
    once the spec is readable."""
    settings = settings or get_settings()
    deps = deps or default_deps()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "replays").mkdir(exist_ok=True)
    log = _WorkerLog(output_dir / "worker.log")
    try:
        spec = _load_spec(spec_path or inputs_dir / "spec.json")
    except (OSError, ValidationError, ValueError):
        log.warning("replay.batch.bad_spec")
        write_json_direct(output_dir / "exit.json", {"exit_code": EXIT_SANDBOX})
        log.close()
        return EXIT_SANDBOX
    with bind(batch_id=spec.batch_id, edit_id=spec.edit_id):
        write_json_direct(output_dir / "spec.json", spec)
        write_json_direct(output_dir / "result.json", build_result(spec, {}))
        log.info("replay.batch.start", n=len(spec.golden_run_ids), sandbox=spec.sandbox)
        outcomes: dict[str, GoldenOutcome] = {}
        lost = "worker_lost"
        n = len(spec.golden_run_ids)
        try:
            with (
                _scratch() as scratch,
                _Heartbeat(output_dir / "progress.json", n, heartbeat_s) as hb,
            ):
                ctx = _context(spec, inputs_dir, output_dir, scratch, settings, deps)
                _run_batch(ctx, outcomes, hb, log)
        except SandboxError as exc:
            log.warning("replay.batch.inputs_failed", code=exc.code)
            lost = "sandbox_failed"
        except WorkerTerminated:
            log.warning("replay.batch.terminated")
        code = _finish(output_dir, build_result(spec, outcomes, lost_code=lost), log)
    log.close()
    return code


def _on_sigterm(signum: int, frame: FrameType | None) -> None:
    raise WorkerTerminated()


def cli(argv: list[str] | None = None) -> int:
    """``python -m ratchet.replay.worker --inputs /in --output /out [--spec F] [--deps X]``."""
    parser = argparse.ArgumentParser(prog="ratchet.replay.worker")
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spec", type=Path, help="spec.json override (e.g. an injected file)")
    parser.add_argument("--deps", help="test/dev hook: module:factory returning WorkerDeps")
    args = parser.parse_args(argv)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _on_sigterm)
    deps = load_deps(args.deps) if args.deps else None
    return main(args.inputs, args.output, deps=deps, spec_path=args.spec)


if __name__ == "__main__":
    raise SystemExit(cli())
