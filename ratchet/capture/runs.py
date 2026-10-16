"""Run metadata and trace finalization (CONTRACTS §2).

Before a harness run starts, the adapter writes ``traces/<run_id>/run.json``; the proxy
reads it to enforce the tier policy and inject the seed. When the task ends,
:func:`finalize_trace` merges the proxy's and the mock server's steps, reconciles the
workspace diff into ``fs.write`` / ``fs.delete`` tool calls, packs the changed and new
files into ``traces/<run_id>/artifacts.tar.zst`` (``Trace.artifacts_ref``), and
saves the trace.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ratchet import clock
from ratchet.log import get_logger
from ratchet.models import EnvSnapshot, Purpose, Step, Tier, ToolCall, Trace, WorldFact
from ratchet.store import Store
from ratchet.store.archive import pack_dir, tree_digest
from ratchet.store.base import write_json_atomic
from ratchet.util import hash_json, preview

__all__ = [
    "RunMeta",
    "write_run_meta",
    "read_run_meta",
    "run_meta_path",
    "workspace_changes",
    "finalize_trace",
    "ARTIFACTS_NAME",
]

_log = get_logger("capture")
_PREVIEW_READ_BYTES = 4096
ARTIFACTS_NAME = "artifacts.tar.zst"


class RunMeta(BaseModel):
    """``traces/<run_id>/run.json`` (CONTRACTS §2)."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    agent_tier: Tier
    seed: int
    frozen_time: datetime | None = None
    harness: str = "minimal"
    edit_id: str | None = None
    purpose: Purpose = "agent"


def run_meta_path(store: Store, run_id: str) -> Path:
    return store.trace_dir(run_id) / "run.json"


def write_run_meta(store: Store, meta: RunMeta) -> Path:
    path = run_meta_path(store, meta.run_id)
    write_json_atomic(path, meta)
    return path


def read_run_meta(store: Store, run_id: str) -> RunMeta | None:
    """The run's metadata, or ``None`` if the run was never announced (or is unreadable)."""
    path = run_meta_path(store, run_id)
    if not path.exists():
        return None
    try:
        return RunMeta.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, OSError) as exc:
        _log.warning("capture.run_meta.unreadable", run_id=run_id, error=type(exc).__name__)
        return None


def workspace_changes(before: dict[str, str], after: dict[str, str]) -> tuple[list[str], list[str]]:
    """``(written, deleted)``: new or changed paths, and paths gone after the task."""
    written = sorted(p for p, digest in after.items() if before.get(p) != digest)
    deleted = sorted(p for p in before if p not in after)
    return written, deleted


def _file_preview(path: Path) -> str:
    try:
        with path.open("rb") as fh:
            raw = fh.read(_PREVIEW_READ_BYTES)
    except OSError:
        return ""
    return preview(raw.decode("utf-8", errors="replace"))


def _already_recorded(steps: list[Step]) -> set[tuple[str, str]]:
    """``(tool, path)`` pairs a mock server ``/notify`` (or an earlier finalize) recorded."""
    seen: set[tuple[str, str]] = set()
    for step in steps:
        if isinstance(step, ToolCall) and step.tool in {"fs.write", "fs.delete"}:
            path = step.args_norm.get("path")
            if isinstance(path, str):
                seen.add((step.tool, path))
    return seen


def _fs_call(
    store: Store,
    run_id: str,
    tool: Literal["fs.write", "fs.delete"],
    rel: str,
    digest: str | None,
    result_preview: str,
) -> ToolCall:
    args: dict[str, object] = {"path": rel}
    return ToolCall(
        step_id=store.next_step_id(run_id, "t"),
        tool=tool,
        args_norm=args,
        args_hash=hash_json(args),
        result_hash=digest,
        side_effect="write_reversible" if tool == "fs.write" else "destructive",
        source="local",
        cassette_key=None,
        result_preview=result_preview,
        started_at=clock.now(),
    )


def _reconcile_workspace(
    store: Store,
    run_id: str,
    steps: list[Step],
    before_dir: Path | None,
    after_dir: Path | None,
) -> tuple[list[ToolCall], list[str]]:
    """The reconciled ``fs.*`` calls and the paths written (changed or new)."""
    if after_dir is None:
        return [], []
    before = tree_digest(before_dir) if before_dir is not None else {}
    after = tree_digest(after_dir)
    written, deleted = workspace_changes(before, after)
    seen = _already_recorded(steps)
    calls: list[ToolCall] = []
    for rel in written:
        if ("fs.write", rel) not in seen:
            calls.append(
                _fs_call(store, run_id, "fs.write", rel, after[rel], _file_preview(after_dir / rel))
            )
    for rel in deleted:
        if ("fs.delete", rel) not in seen:
            calls.append(_fs_call(store, run_id, "fs.delete", rel, None, ""))
    for call in calls:
        store.append_step(run_id, call)
    return calls, written


def _pack_artifacts(store: Store, run_id: str, after_dir: Path, written: list[str]) -> str | None:
    """Tar the run's changed/new files; the ref is relative to the data dir."""
    if not written:
        return None
    try:
        pack_dir(after_dir, store.trace_dir(run_id) / ARTIFACTS_NAME, only=written)
    except OSError as exc:
        _log.warning("capture.artifacts.failed", run_id=run_id, error=type(exc).__name__)
        return None
    return f"traces/{run_id}/{ARTIFACTS_NAME}"


def finalize_trace(
    store: Store,
    run_id: str,
    *,
    task_input: str,
    final_output: str,
    kind: Literal["live", "golden", "replay"],
    env: EnvSnapshot,
    agent_tier: Tier,
    seed: int,
    skills_invoked: list[str],
    workspace_before: Path | None,
    workspace_after: Path | None,
    golden_of: str | None = None,
    candidate_commit: str | None = None,
    artifacts_ref: str | None = None,
    world_dependent: bool = False,
    world_facts: list[WorldFact] | None = None,
    task_label: str = "",
    harness_ok: bool = True,
    error_code: str | None = None,
    save: bool = True,
) -> Trace:
    """Assemble and (by default) save the run's Trace from its recorded steps."""
    steps = store.collect_steps(run_id)
    fs_calls, written = _reconcile_workspace(
        store, run_id, steps, workspace_before, workspace_after
    )
    if artifacts_ref is None and workspace_after is not None:
        artifacts_ref = _pack_artifacts(store, run_id, workspace_after, written)
    trace = Trace(
        run_id=run_id,
        kind=kind,
        task_input=task_input,
        steps=[*steps, *fs_calls],
        final_output=final_output,
        artifacts_ref=artifacts_ref,
        skills_invoked=sorted(set(skills_invoked)),
        world_dependent=world_dependent,
        world_facts=list(world_facts or []),
        env=env,
        agent_tier=agent_tier,
        seed=seed,
        created_at=clock.now(),
        golden_of=golden_of,
        candidate_commit=candidate_commit,
        task_label=task_label,
        harness_ok=harness_ok,
        error_code=error_code,
    )
    if save:
        store.save_trace(trace)
    _log.info(
        "capture.trace.finalized",
        run_id=run_id,
        kind=kind,
        n_steps=len(trace.steps),
        n_fs_calls=len(fs_calls),
    )
    return trace
