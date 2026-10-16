"""Run metadata and trace finalization with workspace reconciliation."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ratchet import clock
from ratchet.capture.runs import (
    RunMeta,
    finalize_trace,
    read_run_meta,
    workspace_changes,
    write_run_meta,
)
from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.models import EnvSnapshot, LLMCall, ToolCall
from ratchet.store import Store
from ratchet.store.archive import unpack


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    configure(Settings(_env_file=None, ratchet_data_dir=tmp_path / "data"))
    yield Store.open(tmp_path / "data")
    reset_settings()


def _env() -> EnvSnapshot:
    return EnvSnapshot(
        workspace_ref="workspace.tar.zst",
        frozen_time=clock.parse("2026-10-14T12:00:00+00:00"),
        harness_version="minimal/0.1.0",
        skills_commit="abc123",
    )


def _write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_run_meta_round_trips_and_missing_meta_is_none(store: Store) -> None:
    meta = RunMeta(run_id="run_a", agent_tier="super", seed=7, harness="minimal", edit_id=None)
    path = write_run_meta(store, meta)
    assert path == store.trace_dir("run_a") / "run.json"
    assert read_run_meta(store, "run_a") == meta
    assert read_run_meta(store, "run_missing") is None


def test_unreadable_run_meta_is_treated_as_missing(store: Store) -> None:
    path = store.trace_dir("run_bad") / "run.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert read_run_meta(store, "run_bad") is None


def test_workspace_changes_lists_new_changed_and_deleted_paths() -> None:
    before = {"a.txt": "1", "b.txt": "2", "gone.txt": "3"}
    after = {"a.txt": "1", "b.txt": "22", "new.txt": "4"}
    assert workspace_changes(before, after) == (["b.txt", "new.txt"], ["gone.txt"])


def test_finalize_trace_merges_steps_and_reconciles_workspace(store: Store, tmp_path: Path) -> None:
    run_id = "run_fin"
    llm = LLMCall(
        step_id=store.next_step_id(run_id, "m"),
        tier="super",
        model="m",
        request_hash="h",
        messages_ref="steps/m001.req.json",
        response_ref="steps/m001.res.json",
        prompt_tokens=1,
        completion_tokens=1,
        usd=0.0,
        latency_ms=1,
        started_at=clock.now(),
    )
    store.append_step(run_id, llm)
    before = _write(tmp_path / "before", {"keep.txt": "same", "edit.md": "v1", "old.log": "x"})
    after = _write(tmp_path / "after", {"keep.txt": "same", "edit.md": "v2", "out/report.md": "r"})

    trace = finalize_trace(
        store,
        run_id,
        task_input="do it",
        final_output="done",
        kind="live",
        env=_env(),
        agent_tier="super",
        seed=3,
        skills_invoked=["triage", "triage"],
        workspace_before=before,
        workspace_after=after,
    )

    tools = [s for s in trace.steps if isinstance(s, ToolCall)]
    assert [(t.tool, t.args_norm["path"], t.side_effect) for t in tools] == [
        ("fs.write", "edit.md", "write_reversible"),
        ("fs.write", "out/report.md", "write_reversible"),
        ("fs.delete", "old.log", "destructive"),
    ]
    assert all(t.source == "local" and t.step_id.startswith("t") for t in tools)
    assert len({t.step_id for t in tools}) == 3
    assert tools[0].result_preview == "v2" and tools[0].result_hash
    assert trace.steps[0].step_id == "m001"
    assert trace.skills_invoked == ["triage"]
    assert store.load_trace(run_id) == trace


def test_finalize_trace_packs_changed_and_new_files_as_artifacts(
    store: Store, tmp_path: Path
) -> None:
    """``artifacts.tar.zst`` holds exactly the written files; ref on the trace."""
    before = _write(tmp_path / "before", {"keep.txt": "same", "edit.md": "v1", "old.log": "x"})
    after = _write(tmp_path / "after", {"keep.txt": "same", "edit.md": "v2", "out/report.md": "r"})
    trace = finalize_trace(
        store,
        "run_art",
        task_input="t",
        final_output="o",
        kind="live",
        env=_env(),
        agent_tier="super",
        seed=1,
        skills_invoked=[],
        workspace_before=before,
        workspace_after=after,
    )
    assert trace.artifacts_ref == "traces/run_art/artifacts.tar.zst"
    archive = store.data_dir / trace.artifacts_ref
    assert archive.is_file()
    out = tmp_path / "unpacked"
    assert sorted(unpack(archive, out)) == ["edit.md", "out/report.md"]
    assert (out / "edit.md").read_text() == "v2"
    unchanged = finalize_trace(
        store,
        "run_noart",
        task_input="t",
        final_output="o",
        kind="live",
        env=_env(),
        agent_tier="super",
        seed=1,
        skills_invoked=[],
        workspace_before=before,
        workspace_after=before,
    )
    assert unchanged.artifacts_ref is None


def test_finalize_twice_does_not_duplicate_workspace_calls(store: Store, tmp_path: Path) -> None:
    after = _write(tmp_path / "after", {"a.txt": "1"})
    kwargs: dict[str, object] = {
        "task_input": "t",
        "final_output": "o",
        "kind": "replay",
        "env": _env(),
        "agent_tier": "super",
        "seed": 1,
        "skills_invoked": [],
        "workspace_before": None,
        "workspace_after": after,
    }
    first = finalize_trace(store, "run_twice", **kwargs)  # type: ignore[arg-type]
    second = finalize_trace(store, "run_twice", **kwargs)  # type: ignore[arg-type]
    assert len(first.steps) == len(second.steps) == 1


def test_finalize_without_workspace_after_skips_reconciliation(store: Store) -> None:
    trace = finalize_trace(
        store,
        "run_nows",
        task_input="t",
        final_output="o",
        kind="live",
        env=_env(),
        agent_tier="nano",
        seed=0,
        skills_invoked=[],
        workspace_before=None,
        workspace_after=None,
        save=False,
    )
    assert trace.steps == [] and not store.has_trace("run_nows")
