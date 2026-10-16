"""SimulateService without the pipeline: session repos, queue, reset, replayed mode."""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import T0, make_settings, make_verdict, open_store, pair, seeded_repo  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.config.settings import Settings  # noqa: E402
from ratchet.errors import ForbiddenError, NotFoundError  # noqa: E402
from ratchet.models import SkillEdit  # noqa: E402
from ratchet.orchestrator.context import load_options  # noqa: E402
from ratchet.orchestrator.recorded import record_fixture  # noqa: E402
from ratchet.orchestrator.simulate import (  # noqa: E402
    SimulateBusy,
    SimulateService,
    session_root,
)
from ratchet.store import Store  # noqa: E402
from ratchet.watcher.gitops import SkillsRepo  # noqa: E402

SESSION = "0123456789abcdef"


class FakeOrchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.submitted: list[SkillEdit] = []
        self.listeners: list[object] = []

    def submit_edit(self, edit: SkillEdit, diff: str) -> None:
        self.store.save_edit(edit, diff)
        self.submitted.append(edit)

    def add_listener(self, listener: object) -> None:
        self.listeners.append(listener)


def _service(tmp_path: Path, **overrides: object) -> tuple[SimulateService, FakeOrchestrator]:
    settings = make_settings(tmp_path, **overrides)
    store = open_store(settings)
    seeded_repo(settings)
    fake = FakeOrchestrator(store)
    return SimulateService(settings, store, fake), fake  # type: ignore[arg-type]


async def test_start_seeds_a_session_repo_and_submits_the_edit(tmp_path: Path) -> None:
    service, fake = _service(tmp_path)
    start = await service.start("1", SESSION)
    assert start.status == "running" and start.edit_id and start.verdict_id
    (edit,) = fake.submitted
    assert edit.author == "seeded" and edit.session_hash == SESSION
    assert edit.affected_skills == ["github-triage"]
    options = load_options(service.store, edit.edit_id)
    repo = SkillsRepo(Path(str(options.repo_dir)))
    assert repo.main_dir.is_relative_to(session_root(service.store, SESSION))
    assert "not_planned" in (repo.candidate_dir / "github-triage" / "SKILL.md").read_text()
    operator = SkillsRepo(service.settings.ratchet_skills_repo)
    assert operator.main_head() == operator.candidate_head()  # operator repo untouched
    assert (await service.start("1", SESSION)).edit_id == start.edit_id  # still running


async def test_scenario_three_gets_an_overlay_and_scoped_tavily(tmp_path: Path) -> None:
    service, fake = _service(tmp_path)
    await service.start("3", SESSION)
    options = load_options(service.store, fake.submitted[0].edit_id)
    assert options.world_ref and Path(options.world_ref).is_dir()
    assert options.tavily_dir and options.tavily_dir.endswith("3/tavily")


async def test_unknown_and_disabled_scenarios_are_refused(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(NotFoundError):
        await service.start("42", SESSION)
    off, _ = _service(tmp_path / "off", ratchet_simulate_enabled=False)
    with pytest.raises(ForbiddenError) as info:
        await off.start("1", SESSION)
    assert info.value.code == "simulate_disabled"


async def test_runs_beyond_the_limit_queue_and_start_when_a_slot_frees(tmp_path: Path) -> None:
    service, fake = _service(tmp_path, ratchet_simulate_max_sessions=1)
    first = await service.start("1", SESSION)
    queued = await service.start("2", "f" * 16)
    assert queued.status == "queued" and queued.queue_position == 1 and queued.eta_s == 60
    store = service.store
    store.set_edit_state(str(first.edit_id), "done")
    row = next(r for r in store.sessions() if r.session_hash == SESSION)
    from dataclasses import replace

    store.upsert_session(replace(row, status="done"))
    service._on_edit_done(str(first.edit_id), "done")
    await service.drain()
    assert len(fake.submitted) == 2 and fake.submitted[1].session_hash == "f" * 16


async def test_a_full_queue_refuses_with_simulate_busy(tmp_path: Path) -> None:
    """The queue is bounded (``ratchet_simulate_max_queue``) → 429 simulate_busy."""
    service, fake = _service(
        tmp_path, ratchet_simulate_max_sessions=1, ratchet_simulate_max_queue=2
    )
    await service.start("1", SESSION)
    assert (await service.start("1", "a" * 16)).status == "queued"
    assert (await service.start("1", "b" * 16)).status == "queued"
    with pytest.raises(SimulateBusy) as info:
        await service.start("1", "c" * 16)
    assert info.value.code == "simulate_busy" and info.value.http_status == 429
    assert len(service.store.sessions(status="queued")) == 2 and len(fake.submitted) == 1


async def test_one_active_or_queued_run_per_client_key(tmp_path: Path) -> None:
    """A client bucket (IP) holds at most one active-or-queued run; new sessions from
    the same client are refused until it finishes, other clients are not affected."""
    service, fake = _service(tmp_path)
    first = await service.start("1", SESSION, client_key="203.0.113.7")
    again = await service.start("1", SESSION, client_key="203.0.113.7")
    assert again.edit_id == first.edit_id  # the same run is idempotent, not refused
    with pytest.raises(SimulateBusy):
        await service.start("2", SESSION, client_key="203.0.113.7")
    with pytest.raises(SimulateBusy):
        await service.start("1", "d" * 16, client_key="203.0.113.7")  # fresh cookie, same IP
    assert (await service.start("2", "e" * 16, client_key="198.51.100.1")).status == "running"
    service.store.set_edit_state(str(first.edit_id), "done")
    assert (await service.start("2", SESSION, client_key="203.0.113.7")).status == "running"
    assert len(fake.submitted) == 3


async def test_reset_deletes_expired_repos_but_not_running_ones(tmp_path: Path) -> None:
    service, fake = _service(tmp_path, ratchet_simulate_reset_s=0)
    await service.start("1", SESSION)
    await service.start("2", "e" * 16)
    service.store.set_edit_state(fake.submitted[0].edit_id, "done")
    assert await service.reset_expired() == 1
    assert not session_root(service.store, SESSION).exists()
    assert session_root(service.store, "e" * 16).is_dir()  # still in flight
    statuses = {r.session_hash: r.status for r in service.store.sessions()}
    assert statuses == {SESSION: "reset", "e" * 16: "running"}


def _record(settings: Settings, store: Store, tmp_path: Path) -> Path:
    repo = SkillsRepo(settings.ratchet_skills_repo)
    from orch_fakes import commit_edit

    edit, diff = commit_edit(repo, {"github-triage/NOTE.md": "x\n"}, session_hash="old" * 4)
    store.save_edit(edit, diff)
    verdict = make_verdict(edit, pairs=[pair("run_g1", "run_r1", "regression")], decision="BLOCK")
    store.save_verdict(verdict)
    for kind, at in (("orchestrator.state", 0), ("batch.heartbeat", 1), ("verdict.decided", 2)):
        with clock.frozen(T0 + timedelta(milliseconds=at * 100)):
            payload: dict[str, object] = {"edit_id": edit.edit_id, "to": "collecting"}
            ref = verdict.verdict_id if kind.startswith("verdict") else edit.edit_id
            store.emit(kind, ref, payload, session_hash=edit.session_hash)
    return record_fixture(store, settings, verdict.verdict_id, "1", dest=tmp_path / "rec" / "1")


async def test_replayed_mode_serves_a_recorded_run_under_fresh_ids(tmp_path: Path) -> None:
    service, fake = _service(tmp_path / "a")
    out = _record(service.settings, service.store, tmp_path)
    assert {p.name for p in out.iterdir()} >= {"verdict.json", "edit.json", "events.json"}
    scenarios = tmp_path / "scen"
    (scenarios / "recorded").mkdir(parents=True)
    import shutil

    shutil.copytree(service.settings.ratchet_scenarios_dir / "1", scenarios / "1")
    shutil.copytree(out, scenarios / "recorded" / "1")
    replayed, _ = _service(
        tmp_path / "b", ratchet_simulate_mode="replayed", ratchet_scenarios_dir=scenarios
    )
    start = await replayed.start("1", SESSION)
    assert start.status == "running" and not _.submitted
    for task in list(replayed._background):
        await task
    verdict = replayed.store.load_verdict(str(start.verdict_id))
    assert verdict.recorded and verdict.session_hash == SESSION
    assert verdict.edit_id == start.edit_id and verdict.decision == "BLOCK"
    events = replayed.store.events(session_hash=SESSION, include_global=False)
    assert [e.kind for e in events] == ["orchestrator.state", "batch.heartbeat", "verdict.decided"]
    assert all(e.payload.get("edit_id") == start.edit_id for e in events)
    assert replayed.store.edit_state(str(start.edit_id)).state == "done"


async def test_the_committed_recordings_replay_with_their_labels(tmp_path: Path) -> None:
    """scenarios/recorded/<k>/ (from `ratchet simulate k --record-fixture`) stay servable."""
    service, fake = _service(tmp_path, ratchet_simulate_mode="replayed")
    start = await service.start("3", SESSION)
    for task in list(service._background):
        await task
    verdict = service.store.load_verdict(str(start.verdict_id))
    recorded = json.loads(
        (Path(service.settings.ratchet_scenarios_dir) / "recorded/3/verdict.json").read_text()
    )
    # A recording keeps the label it was made with (live Nemotron or the simulator).
    assert verdict.recorded and not fake.submitted
    assert verdict.simulated_models is recorded["simulated_models"]
    assert verdict.decision == "PASS" and "drift" in verdict.reasons
    for pair_ in verdict.pairs:
        assert service.store.load_trace(pair_.replay_run_id).kind == "replay"
        assert service.store.load_trace(pair_.golden_run_id).archived_reason == "recorded"
    assert {"verdict.decided", "simulate.finished"} <= {
        e.kind for e in service.store.events(session_hash=SESSION, include_global=False)
    }
