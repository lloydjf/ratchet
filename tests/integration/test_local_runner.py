"""Local job runner — batch spec in → result.json out (process mode, fake sidecars)."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

from ratchet.config.settings import Settings
from ratchet.replay.jobs import JobStatus, LocalJobRunner
from ratchet.replay.packing import import_batch_output, pack_batch_inputs
from ratchet.store import Store

FAKES_PATH = Path(__file__).resolve().parent / "test_worker_fakes.py"


def _fakes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_fakes", FAKES_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _wait(runner: LocalJobRunner, job_id: str, timeout_s: float = 90.0) -> JobStatus:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        status = await runner.poll(job_id)
        if status.terminal or loop.time() > deadline:
            return status
        await asyncio.sleep(0.2)


async def test_local_process_runner_turns_a_batch_spec_into_a_result(tmp_path: Path) -> None:
    fakes = _fakes()
    store = Store.open(tmp_path / "control")
    fakes.make_golden(store, "run_g1", "Triage the open issues", workspace={"a.txt": "x"})
    spec = fakes.make_spec(["run_g1"])
    inputs = pack_batch_inputs(store, spec, tmp_path / "in")
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "worker-data")  # type: ignore[call-arg]
    runner = LocalJobRunner(
        "process",
        settings=settings,
        work_root=tmp_path / "jobs",
        worker_args=["--deps", f"{FAKES_PATH}:make_deps"],
    )

    job_id = await runner.submit(spec, inputs)
    assert await runner.submit(spec, inputs) == job_id  # idempotent by batch_id
    status = await _wait(runner, job_id)
    assert status.state == "succeeded" and status.exit_code == 0
    assert (status.k, status.n) == (1, 1) and status.heartbeat_at is not None

    output = await runner.fetch(job_id, tmp_path / "fetched")
    result = import_batch_output(store, output)
    assert result.status == "ok"
    (replay,) = result.replays
    assert store.load_trace(replay.run_id).golden_of == "run_g1"
    assert any(s.kind == "tool" and s.tool == "github.list_issues" for s in replay.steps)
    assert store.spend().total_usd > 0
