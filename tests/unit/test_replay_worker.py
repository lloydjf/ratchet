"""Replay worker (the worker contract) run in-process with fake sidecars."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from ratchet.config.settings import Settings
from ratchet.models import ReplayResult
from ratchet.replay import worker
from ratchet.replay.packing import pack_batch_inputs
from ratchet.store import Store

FAKES_PATH = Path(__file__).resolve().parents[1] / "integration" / "test_worker_fakes.py"


def _fakes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_fakes", FAKES_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def fakes() -> ModuleType:
    return _fakes()


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        ratchet_data_dir=tmp_path / "data",
        nebius_token_factory_key="tf-real-secret-key-123456",
    )


def _batch(tmp_path: Path, fakes: ModuleType, tasks: dict[str, str]) -> tuple[Path, Store]:
    store = Store.open(tmp_path / "control")
    for run_id, task in tasks.items():
        fakes.make_golden(store, run_id, task, workspace={"notes.txt": "hello\n"})
    spec = fakes.make_spec(list(tasks), timeout_s=60 * len(tasks))
    inputs = pack_batch_inputs(store, spec, tmp_path / "in")
    return inputs, store


def _result(out: Path) -> ReplayResult:
    return ReplayResult.model_validate_json((out / "result.json").read_text())


def test_worker_replays_each_golden_and_writes_result(tmp_path, fakes, settings):
    inputs, _ = _batch(tmp_path, fakes, {"run_g1": "Triage the open issues in acme/widgets"})
    out = tmp_path / "out"
    code = worker.main(inputs, out, deps=fakes.make_deps(), settings=settings)
    result = _result(out)
    assert code == worker.EXIT_OK
    assert result.status == "ok" and result.failed_goldens == {}
    (replay,) = result.replays
    assert replay.kind == "replay" and replay.golden_of == "run_g1"
    assert replay.candidate_commit == "def456"
    assert replay.final_output.startswith("Done:")
    assert replay.skills_invoked == ["triage-issues"]
    assert [s.kind for s in replay.steps] == ["llm", "tool", "llm"]
    assert (out / "replays" / replay.run_id / "trace.json").is_file()
    assert (out / "replays" / replay.run_id / "harness" / "stdout.txt").is_file()
    progress = json.loads((out / "progress.json").read_text())
    assert progress["k"] == 1 and progress["n"] == 1 and progress["heartbeat_at"]
    assert json.loads((out / "exit.json").read_text()) == {"exit_code": 0}


def test_worker_isolates_a_missing_workspace_as_sandbox_failed(tmp_path, fakes, settings):
    inputs, _ = _batch(tmp_path, fakes, {"run_g1": "Triage issues", "run_g2": "Triage again"})
    (inputs / "workspaces" / "run_g2.tar.zst").unlink()
    out = tmp_path / "out"
    code = worker.main(inputs, out, deps=fakes.make_deps(), settings=settings)
    result = _result(out)
    assert code == worker.EXIT_PARTIAL
    assert result.status == "partial"
    assert result.failed_goldens == {"run_g2": "sandbox_failed"}
    assert len(result.replays) == 1


def test_worker_records_harness_crash_with_a_trace(tmp_path, fakes, settings):
    inputs, _ = _batch(tmp_path, fakes, {"run_g1": "CRASH please"})
    out = tmp_path / "out"
    code = worker.main(inputs, out, deps=fakes.make_deps(), settings=settings)
    result = _result(out)
    assert code == worker.EXIT_HARNESS
    assert result.status == "failed"
    assert result.failed_goldens == {"run_g1": "harness_failed"}
    (replay,) = result.replays
    assert replay.harness_ok is False and replay.error_code == "harness_failed"


def test_worker_reports_blocked_sends_as_unexpected_side_effects(tmp_path, fakes, settings):
    inputs, _ = _batch(tmp_path, fakes, {"run_g1": "SEND the report"})
    out = tmp_path / "out"
    assert worker.main(inputs, out, deps=fakes.make_deps(), settings=settings) == 0
    result = _result(out)
    (replay,) = result.replays
    (blocked,) = result.unexpected_by_run[replay.run_id]
    assert blocked.tool == "gmail.send" and blocked.side_effect == "external_send"
    assert result.unexpected_side_effects == [blocked]
    assert "blocked by Ratchet" in replay.final_output


def test_worker_with_unreadable_spec_exits_sandbox_failure(tmp_path, fakes, settings):
    inputs = tmp_path / "in"
    inputs.mkdir()
    (inputs / "spec.json").write_text("{not json")
    code = worker.main(inputs, tmp_path / "out", deps=fakes.make_deps(), settings=settings)
    assert code == worker.EXIT_SANDBOX


def test_worker_without_skills_tarball_fails_every_golden(tmp_path, fakes, settings):
    inputs, _ = _batch(tmp_path, fakes, {"run_g1": "Triage"})
    (inputs / "skills.tar.zst").unlink()
    out = tmp_path / "out"
    code = worker.main(inputs, out, deps=fakes.make_deps(), settings=settings)
    assert code == worker.EXIT_SANDBOX
    assert _result(out).failed_goldens == {"run_g1": "sandbox_failed"}


def test_process_sandbox_is_refused_in_public_mode(tmp_path, fakes, settings):
    inputs, _ = _batch(tmp_path, fakes, {"run_g1": "Triage"})
    public = settings.model_copy(update={"ratchet_public_mode": True})
    out = tmp_path / "out"
    assert worker.main(inputs, out, deps=fakes.make_deps(), settings=public) == 4
    assert _result(out).failed_goldens == {"run_g1": "sandbox_failed"}


def test_exit_code_prefers_budget_over_partial(fakes):
    spec = fakes.make_spec(["a", "b"])
    from ratchet.replay.replay_one import GoldenOutcome

    outcomes = {"a": GoldenOutcome("a", None, [], "budget_exceeded")}
    result = worker.build_result(spec, outcomes)
    assert result.failed_goldens == {"a": "budget_exceeded", "b": "worker_lost"}
    assert worker.exit_code_for(result) == worker.EXIT_BUDGET


def test_worker_output_survives_a_mount_that_breaks_rename(tmp_path, fakes, settings, monkeypatch):
    """A Serverless Job's output volume is an object-storage mount: a rename leaves the
    content on the temp name and an empty final object. Output files are written in place."""
    import pathlib

    inputs, _store = _batch(tmp_path, fakes, {"run_a": "hello"})
    out = tmp_path / "mount" / "out"
    real_replace = pathlib.Path.replace

    def broken_replace(self: pathlib.Path, target):  # type: ignore[no-untyped-def]
        target = pathlib.Path(target)
        if out in target.parents or target == out:
            target.write_bytes(b"")  # what the mount does
            self.unlink()
            return target
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", broken_replace)
    code = worker.main(inputs, out, deps=fakes.make_deps(), settings=settings)
    assert code == 0
    for name in ("result.json", "exit.json", "progress.json", "spec.json"):
        assert (out / name).stat().st_size > 0, name
    assert not list(out.glob(".*.tmp"))
    assert json.loads((out / "exit.json").read_text())["exit_code"] == 0
