"""Batch packing round trip: control-plane store → worker inputs → worker output → store."""

from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from ratchet.errors import NotFoundError
from ratchet.models import ReplayResult
from ratchet.replay.packing import cassette_addresses, import_batch_output, pack_batch_inputs
from ratchet.store import Store
from ratchet.store.archive import list_files, unpack

FAKES = Path(__file__).resolve().parents[1] / "integration" / "test_worker_fakes.py"


@pytest.fixture()
def fakes() -> ModuleType:
    spec = importlib.util.spec_from_file_location("worker_fakes", FAKES)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pack_writes_the_worker_contract_inputs(tmp_path, fakes):
    store = Store.open(tmp_path / "data")
    golden = fakes.make_golden(store, "run_g1", "Triage", workspace={"a/b.txt": "hi"})
    world = tmp_path / "world"
    (world / "finance.rate").mkdir(parents=True)
    (world / "finance.rate" / "x.json").write_text("{}")
    spec = fakes.make_spec(["run_g1"])
    inputs = pack_batch_inputs(store, spec, tmp_path / "in", world_dir=world)

    assert (inputs / "spec.json").is_file() and (inputs / "goldens" / "run_g1.json").is_file()
    assert cassette_addresses([golden]) == [("github.list_issues", fakes.CASSETTE_KEY)]
    unpack(inputs / "cassettes.tar.zst", tmp_path / "c")
    assert list_files(tmp_path / "c") == [f"github.list_issues/{fakes.CASSETTE_KEY}.json"]
    unpack(inputs / "workspaces" / "run_g1.tar.zst", tmp_path / "ws")
    assert (tmp_path / "ws" / "a" / "b.txt").read_text() == "hi"
    unpack(inputs / "skills.tar.zst", tmp_path / "sk")
    assert (tmp_path / "sk" / "triage-issues" / "SKILL.md").is_file()
    assert (inputs / "world.tar.zst").is_file()


def test_pack_uses_an_empty_workspace_when_the_golden_has_none(tmp_path, fakes):
    store = Store.open(tmp_path / "data")
    fakes.make_golden(store, "run_g1", "Triage", workspace={})
    (store.trace_dir("run_g1") / "workspace.tar.zst").unlink()
    inputs = pack_batch_inputs(store, fakes.make_spec(["run_g1"]), tmp_path / "in")
    assert unpack(inputs / "workspaces" / "run_g1.tar.zst", tmp_path / "ws") == []


def test_import_copies_traces_indexes_them_and_records_costs(tmp_path, fakes):
    from ratchet.config.settings import Settings
    from ratchet.replay import worker

    control = Store.open(tmp_path / "control")
    fakes.make_golden(control, "run_g1", "Triage", workspace={"x": "1"})
    spec = fakes.make_spec(["run_g1"])
    inputs = pack_batch_inputs(control, spec, tmp_path / "in")
    out = tmp_path / "out"
    settings = Settings(_env_file=None, ratchet_data_dir=tmp_path / "wd")  # type: ignore[call-arg]
    assert worker.main(inputs, out, deps=fakes.make_deps(), settings=settings) == 0

    result = import_batch_output(control, out)
    (replay,) = result.replays
    assert control.load_trace(replay.run_id).golden_of == "run_g1"
    assert (control.trace_dir(replay.run_id) / "harness" / "stdout.txt").is_file()
    assert [r.run_id for r in control.list_traces(kind="replay")] == [replay.run_id]
    assert control.spend(edit_id="edit_t1").total_usd == pytest.approx(0.002)
    assert control.load_batch_result(spec.batch_id) == result
    assert (control.batch_dir(spec.batch_id) / "worker.log").is_file()
    import_batch_output(control, out)  # idempotent
    assert control.spend(edit_id="edit_t1").total_usd == pytest.approx(0.002)


def test_import_without_result_marks_every_golden_lost(tmp_path, fakes):
    control = Store.open(tmp_path / "control")
    spec = fakes.make_spec(["run_a", "run_b"])
    control.save_batch_spec(spec, "local")
    out = tmp_path / "out"
    out.mkdir()
    result = import_batch_output(control, out, batch_id=spec.batch_id)
    assert result.status == "failed"
    assert result.failed_goldens == {"run_a": "worker_lost", "run_b": "worker_lost"}
    shutil.rmtree(out)
    out.mkdir()
    with pytest.raises(NotFoundError):
        import_batch_output(control, out)
    assert isinstance(result, ReplayResult)


def test_import_with_an_empty_result_marks_every_golden_lost(tmp_path, fakes):
    """An object-storage mount can leave ``result.json`` empty: that is a lost worker,
    never a crash in the collector."""
    control = Store.open(tmp_path / "control")
    spec = fakes.make_spec(["run_a", "run_b"])
    control.save_batch_spec(spec, "nebius")
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_bytes(b"")
    (out / "spec.json").write_bytes(b"")
    result = import_batch_output(control, out, batch_id=spec.batch_id)
    assert result.status == "failed"
    assert result.failed_goldens == {"run_a": "worker_lost", "run_b": "worker_lost"}
