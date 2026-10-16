"""The Orchestrator queue: FIFO, max in flight, skill-overlap waits, resume, replay."""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import commit_edit, make_settings, open_store, seeded_repo  # noqa: E402

from ratchet.errors import ConflictError  # noqa: E402
from ratchet.models import SkillEdit  # noqa: E402
from ratchet.orchestrator import service as service_mod  # noqa: E402
from ratchet.orchestrator.context import EditOptions, Services, load_options, save_options  # noqa: E402
from ratchet.orchestrator.service import Orchestrator  # noqa: E402
from ratchet.store import Store  # noqa: E402


class FakePipeline:
    """Stands in for ``run_edit``: records order and holds each edit until released."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}

    async def __call__(self, services: Services, edit_id: str) -> str:
        self.started.append(edit_id)
        gate = self.gates.setdefault(edit_id, asyncio.Event())
        await gate.wait()
        services.store.set_edit_state(edit_id, "done")
        return "done"

    def release(self, edit_id: str) -> None:
        self.gates.setdefault(edit_id, asyncio.Event()).set()


def _orchestrator(tmp_path: Path, **overrides: object) -> tuple[Orchestrator, Store]:
    settings = make_settings(tmp_path, **overrides)
    store = open_store(settings)
    seeded_repo(settings)
    return Orchestrator(settings, store, digest_enabled=False), store


def _edit(tmp_path: Path, skill: str, n: int) -> tuple[SkillEdit, str]:
    """One edit touching only ``skill`` (diffed against the previous candidate head)."""
    repo = seeded_repo(make_settings(tmp_path))
    return commit_edit(repo, {f"{skill}/NOTE{n}.md": f"note {n}\n"}, stacked=True)


async def _settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0.01)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakePipeline:
    pipeline = FakePipeline()
    monkeypatch.setattr(service_mod, "run_edit", pipeline)
    return pipeline


async def test_submit_saves_emits_and_runs_the_edit(tmp_path: Path, fake: FakePipeline) -> None:
    orchestrator, store = _orchestrator(tmp_path)
    await orchestrator.start(watch=False)
    try:
        edit, diff = _edit(tmp_path, "github-triage", 1)
        orchestrator.submit_edit(edit, diff)
        await _settle()
        assert fake.started == [edit.edit_id]
        (event,) = store.events(kinds=["edit.detected"])
        assert event.payload["affected_skills"] == ["github-triage"]
        assert event.payload["verdict_id"] == edit.edit_id.replace("edit_", "verdict_")
        assert store.load_diff(edit.edit_id) == diff
        fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
    finally:
        await orchestrator.stop()


async def test_overlapping_skills_wait_and_others_run_up_to_the_limit(
    tmp_path: Path, fake: FakePipeline
) -> None:
    orchestrator, _ = _orchestrator(tmp_path, ratchet_max_edits_in_flight=2)
    await orchestrator.start(watch=False)
    try:
        a = _edit(tmp_path, "github-triage", 1)
        b = _edit(tmp_path, "github-triage", 2)  # same skill as a: waits
        c = _edit(tmp_path, "expense-report", 3)
        d = _edit(tmp_path, "meeting-notes", 4)  # limit of 2 reached: waits
        for edit, diff in (a, b, c, d):
            orchestrator.submit_edit(edit, diff)
        await _settle()
        assert fake.started == [a[0].edit_id, c[0].edit_id]
        fake.release(a[0].edit_id)
        await _settle()
        assert fake.started[2] == b[0].edit_id  # FIFO: b was first in line
        for edit, _diff in (b, c, d):
            fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
        assert fake.started == [e.edit_id for e, _ in (a, c, b, d)]
    finally:
        await orchestrator.stop()


async def test_session_repos_never_wait_on_each_other(tmp_path: Path, fake: FakePipeline) -> None:
    orchestrator, store = _orchestrator(tmp_path, ratchet_max_edits_in_flight=4)
    await orchestrator.start(watch=False)
    try:
        edits = [_edit(tmp_path, "github-triage", n) for n in (1, 2)]
        for n, (edit, diff) in enumerate(edits):
            store.save_edit(edit, diff)
            save_options(store, edit.edit_id, EditOptions(repo_dir=f"/sessions/{n}/skills"))
            orchestrator.submit_edit(edit, diff)
        await _settle()
        assert len(fake.started) == 2
        for edit, _ in edits:
            fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
    finally:
        await orchestrator.stop()


async def test_submit_is_safe_from_the_watcher_thread(tmp_path: Path, fake: FakePipeline) -> None:
    orchestrator, _ = _orchestrator(tmp_path)
    await orchestrator.start(watch=False)
    try:
        edit, diff = _edit(tmp_path, "github-triage", 1)
        thread = threading.Thread(target=orchestrator.submit_edit, args=(edit, diff))
        thread.start()
        thread.join()
        await _settle()
        assert fake.started == [edit.edit_id]
        fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
    finally:
        await orchestrator.stop()


async def test_start_resumes_every_unfinished_edit(tmp_path: Path, fake: FakePipeline) -> None:
    orchestrator, store = _orchestrator(tmp_path, ratchet_max_edits_in_flight=4)
    first, second = _edit(tmp_path, "github-triage", 1), _edit(tmp_path, "expense-report", 2)
    for edit, diff in (first, second):
        store.save_edit(edit, diff)
    store.set_edit_state(first[0].edit_id, "judging")
    store.set_edit_state(second[0].edit_id, "done")
    await orchestrator.start(watch=False)
    try:
        await _settle()
        assert fake.started == [first[0].edit_id]
        fake.release(first[0].edit_id)
        await orchestrator.wait_idle(5)
    finally:
        await orchestrator.stop()


async def test_replay_starts_a_new_attempt_and_refuses_a_busy_edit(
    tmp_path: Path, fake: FakePipeline
) -> None:
    orchestrator, store = _orchestrator(tmp_path)
    await orchestrator.start(watch=False)
    try:
        edit, diff = _edit(tmp_path, "github-triage", 1)
        orchestrator.submit_edit(edit, diff)
        await _settle()
        with pytest.raises(ConflictError):
            await orchestrator.replay(edit.edit_id)
        fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
        fake.gates.clear()
        await orchestrator.replay(edit.edit_id, runner="local")
        await _settle()
        assert fake.started.count(edit.edit_id) == 2
        options = load_options(store, edit.edit_id)
        assert options.attempt == 2 and options.runner == "local"
        fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
    finally:
        await orchestrator.stop()


async def test_wait_idle_times_out_while_work_is_pending(
    tmp_path: Path, fake: FakePipeline
) -> None:
    orchestrator, _ = _orchestrator(tmp_path)
    await orchestrator.start(watch=False)
    try:
        edit, diff = _edit(tmp_path, "github-triage", 1)
        orchestrator.submit_edit(edit, diff)
        with pytest.raises(TimeoutError):
            await orchestrator.wait_idle(0.2)
        fake.release(edit.edit_id)
        await orchestrator.wait_idle(5)
    finally:
        await orchestrator.stop()
