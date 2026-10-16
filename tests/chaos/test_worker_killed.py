"""A worker killed mid-batch leaves a partial result; unfinished pairs are failed."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

from ratchet.config.settings import Settings
from ratchet.replay.jobs import LocalJobRunner
from ratchet.replay.packing import import_batch_output, pack_batch_inputs
from ratchet.store import Store

FAKES_PATH = Path(__file__).resolve().parents[1] / "integration" / "test_worker_fakes.py"


def _fakes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_fakes", FAKES_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _start(tmp_path: Path) -> tuple[LocalJobRunner, str, Store]:
    fakes = _fakes()
    store = Store.open(tmp_path / "control")
    tasks = {"run_g1": "Triage issues", "run_g2": "SLOW triage", "run_g3": "Triage more"}
    for run_id, task in tasks.items():
        fakes.make_golden(store, run_id, task, workspace={"a.txt": "x"})
    spec = fakes.make_spec(list(tasks), timeout_s=600)
    inputs = pack_batch_inputs(store, spec, tmp_path / "in")
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "wd")  # type: ignore[call-arg]
    runner = LocalJobRunner(
        "process",
        settings=settings,
        work_root=tmp_path / "jobs",
        worker_args=["--deps", f"{FAKES_PATH}:make_deps"],
    )
    job_id = await runner.submit(spec, inputs)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 60
    while (await runner.poll(job_id)).k < 1:
        assert loop.time() < deadline, "first replay never finished"
        await asyncio.sleep(0.2)
    await asyncio.sleep(1.0)  # the second (SLOW) golden is now in flight
    return runner, job_id, store


async def test_worker_sigkill_mid_batch_leaves_partial_result(tmp_path: Path) -> None:
    runner, job_id, store = await _start(tmp_path)
    runner._procs[job_id].kill()
    await asyncio.to_thread(runner._procs[job_id].wait, 10)

    status = await runner.poll(job_id)
    assert status.state == "failed" and status.exit_code is not None and status.exit_code < 0
    result = import_batch_output(store, await runner.fetch(job_id, tmp_path / "fetched"))
    assert result.status == "partial"
    assert [r.golden_of for r in result.replays] == ["run_g1"]
    assert result.failed_goldens == {"run_g2": "worker_lost", "run_g3": "worker_lost"}


async def test_cancelled_worker_checkpoints_and_exits_partial(tmp_path: Path) -> None:
    runner, job_id, store = await _start(tmp_path)
    await runner.cancel(job_id)

    status = await runner.poll(job_id)
    assert status.state == "failed" and status.exit_code == 3
    result = import_batch_output(store, await runner.fetch(job_id, tmp_path / "fetched"))
    assert result.status == "partial"
    assert result.failed_goldens == {"run_g2": "worker_lost", "run_g3": "worker_lost"}
