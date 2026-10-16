"""Batch packing (control plane → worker) and output import (worker → control plane).

``pack_batch_inputs`` writes the worker inputs: ``spec.json``, ``goldens/*.json``,
``cassettes.tar.zst`` (exactly the cassettes the goldens used, by ``ToolCall.cassette_key``),
``skills.tar.zst`` (the candidate checkout), ``workspaces/<run_id>.tar.zst`` (from
``env.workspace_ref``; an empty workspace when a golden has none) and an optional
``world.tar.zst`` overlay.

``import_batch_output`` copies ``replays/<run_id>/`` into the control-plane store, indexes
each replay trace (``save_trace``), records the replays' model-call costs (the worker's own
ledger is scratch) and returns the ``ReplayResult``. A worker that died before writing
``result.json`` yields a ``failed`` result with every golden ``worker_lost``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ratchet.errors import NotFoundError
from ratchet.log import get_logger
from ratchet.models import LLMCall, ReplayBatch, ReplayResult, ToolCall, Trace
from ratchet.store import CostRow, Store
from ratchet.store.archive import pack_dir
from ratchet.store.base import write_json_atomic

__all__ = ["pack_batch_inputs", "import_batch_output", "cassette_addresses", "workspace_tarball"]

_log = get_logger("replay.packing")


def cassette_addresses(goldens: list[Trace]) -> list[tuple[str, str]]:
    """``(tool, cassette_key)`` for every cassette-served call in ``goldens``."""
    return sorted(
        {
            (step.tool, step.cassette_key)
            for golden in goldens
            for step in golden.steps
            if isinstance(step, ToolCall) and step.cassette_key
        }
    )


def workspace_tarball(store: Store, golden: Trace) -> Path | None:
    """Resolve ``env.workspace_ref``: absolute, trace-relative or data-dir-relative."""
    ref = golden.env.workspace_ref
    if not ref:
        return None
    candidates = [Path(ref), store.trace_dir(golden.run_id) / ref, store.data_dir / ref]
    return next((p for p in candidates if p.is_file()), None)


def _pack_skills(source: Path, dest: Path) -> None:
    if source.is_dir():
        pack_dir(source, dest)
    elif source.is_file():
        shutil.copyfile(source, dest)
    else:
        raise NotFoundError(f"skills source {source} not found", code="not_found")


def pack_batch_inputs(
    store: Store,
    spec: ReplayBatch,
    inputs_dir: Path,
    *,
    skills_src: Path | None = None,
    world_dir: Path | None = None,
) -> Path:
    """Write a batch's inputs into ``inputs_dir`` and return it.

    ``skills_src`` is the candidate checkout (a directory) or a ready ``.tar.zst``; it
    defaults to ``spec.skills_tar_ref``. ``world_dir`` defaults to ``spec.world_ref`` when
    that names a directory.
    """
    inputs_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(inputs_dir / "spec.json", spec)
    goldens = [store.load_trace(run_id) for run_id in spec.golden_run_ids]
    for golden in goldens:
        write_json_atomic(inputs_dir / "goldens" / f"{golden.run_id}.json", golden)
        tarball = workspace_tarball(store, golden)
        dest = inputs_dir / "workspaces" / f"{golden.run_id}.tar.zst"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if tarball is None:
            _log.warning("replay.pack.empty_workspace", run_id=golden.run_id)
            pack_dir(inputs_dir / ".empty-workspace", dest)
        else:
            shutil.copyfile(tarball, dest)
    with tempfile.TemporaryDirectory(prefix="ratchet-cassettes-") as tmp:
        copied = store.copy_cassettes(cassette_addresses(goldens), Path(tmp))
        pack_dir(Path(tmp), inputs_dir / "cassettes.tar.zst")
    _pack_skills(skills_src or Path(spec.skills_tar_ref), inputs_dir / "skills.tar.zst")
    world = world_dir or (Path(spec.world_ref) if spec.world_ref else None)
    if world is not None and world.is_dir():
        pack_dir(world, inputs_dir / "world.tar.zst")
    _log.info("replay.pack.done", batch_id=spec.batch_id, n=len(goldens), cassettes=copied)
    return inputs_dir


def _lost_result(store: Store, output_dir: Path, batch_id: str | None) -> ReplayResult:
    spec_path = output_dir / "spec.json"
    if spec_path.is_file():
        spec = ReplayBatch.model_validate_json(spec_path.read_text(encoding="utf-8"))
    elif batch_id is not None:
        spec = store.load_batch_spec(batch_id)
    else:
        raise NotFoundError("worker output has no result.json", code="not_found")
    return ReplayResult(
        batch_id=spec.batch_id,
        replays=[],
        unexpected_side_effects=[],
        worker_log_ref="worker.log",
        status="failed",
        failed_goldens={run_id: "worker_lost" for run_id in spec.golden_run_ids},
    )


def _record_costs(
    store: Store, trace: Trace, edit_id: str | None, session_hash: str | None
) -> None:
    for step in trace.steps:
        if isinstance(step, LLMCall):
            store.record_cost(
                CostRow(
                    call_id=f"{trace.run_id}:{step.step_id}",
                    run_id=trace.run_id,
                    edit_id=edit_id,
                    tier=step.tier,
                    model=step.model,
                    prompt_tokens=step.prompt_tokens,
                    completion_tokens=step.completion_tokens,
                    usd=step.usd,
                    purpose=step.purpose,
                    session_hash=session_hash,
                    cached_prompt_tokens=step.cached_prompt_tokens,
                )
            )


def import_batch_output(
    store: Store,
    output_dir: Path,
    *,
    batch_id: str | None = None,
    session_hash: str | None = None,
) -> ReplayResult:
    """Copy replay traces into ``store``, index them, and return the batch result.

    ``session_hash`` tags the replays' cost rows for a Simulate session."""
    result_path = output_dir / "result.json"
    if not result_path.is_file():
        result = _lost_result(store, output_dir, batch_id)
    else:
        result = ReplayResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    edit_id = None
    if (output_dir / "spec.json").is_file():
        edit_id = ReplayBatch.model_validate_json((output_dir / "spec.json").read_text()).edit_id
    for trace in result.replays:
        source = output_dir / "replays" / trace.run_id
        if source.is_dir():
            shutil.copytree(source, store.trace_dir(trace.run_id), dirs_exist_ok=True)
        store.save_trace(trace)
        _record_costs(store, trace, edit_id, session_hash)
    if (output_dir / "worker.log").is_file():
        batch_dir = store.batch_dir(result.batch_id)
        batch_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output_dir / "worker.log", batch_dir / "worker.log")
    store.save_batch_result(result)
    _log.info("replay.import.done", batch_id=result.batch_id, status=result.status)
    return result
