"""Simulate: a judge presses a scenario button (design §4–§5).

Each run gets its own skills repo, ``data/sessions/<session_hash>/<scenario>/skills``, a
``SkillsRepo.clone_to`` copy of the operator repo's ``main``; the scenario's ``edit/``
files land on that copy's candidate as one ``seeded`` commit and the edit goes through the
normal pipeline with the session hash on every row. A scenario with a ``world/`` dir gets
a cassette overlay (``overlay.build``) and one with ``tavily/`` gets scenario-scoped Tavily
fixtures. At most ``ratchet_simulate_max_sessions`` runs at once; the rest are ``queued``
and start as runs finish. After ``ratchet_simulate_reset_s`` the run's repo is deleted
(the verdict JSON stays, so links keep working). ``RATCHET_SIMULATE_MODE=replayed`` serves
a recorded run instead (``ratchet.orchestrator.recorded``); no model is called.
"""

from __future__ import annotations

import asyncio
import shutil
import weakref
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from ratchet import clock
from ratchet.config.settings import Settings
from ratchet.copy import ERROR_COPY, SIMULATE_BUSY_CLIENT
from ratchet.errors import ConflictError, ForbiddenError, NotFoundError, RatchetError
from ratchet.judge.tavily import TavilySource
from ratchet.log import get_logger
from ratchet.models import Trace
from ratchet.orchestrator import overlay
from ratchet.orchestrator.context import EditOptions, operator_repo, save_options
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.orchestrator.journal import TERMINAL
from ratchet.orchestrator.live_world import WorldChoice, choose
from ratchet.orchestrator.scenarios import Scenario, ScenarioInfo, get_scenario, load_scenarios
from ratchet.store import SessionRow, Store
from ratchet.watcher.edits import build_edit
from ratchet.watcher.gitops import SkillsRepo

if TYPE_CHECKING:
    from ratchet.orchestrator.service import Orchestrator

__all__ = [
    "ScenarioInfo",
    "SimulateStart",
    "SimulateService",
    "SimulateBusy",
    "session_root",
    "ETA_PER_RUN_S",
]

ETA_PER_RUN_S = 60
_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)
_log = get_logger("simulate")
_BUSY_QUEUE = ERROR_COPY["simulate_busy"]
_BUSY_CLIENT = SIMULATE_BUSY_CLIENT


class SimulateBusy(RatchetError):
    """The Simulate queue is full, or this client already has a run in flight."""

    code = "simulate_busy"
    http_status = 429


class SimulateStart(BaseModel):
    session_hash: str
    scenario_id: str
    edit_id: str | None
    verdict_id: str | None
    status: Literal["running", "queued", "done"]
    queue_position: int | None = None
    eta_s: int | None = None


def _guard() -> asyncio.Lock:
    """One start/promote/reset at a time per event loop (session rows are the state)."""
    loop = asyncio.get_running_loop()
    lock = _locks.get(loop)
    if lock is None:
        lock = _locks[loop] = asyncio.Lock()
    return lock


def session_root(store: Store, session_hash: str) -> Path:
    return store.data_dir / "sessions" / session_hash


class SimulateService:
    def __init__(self, settings: Settings, store: Store, orchestrator: Orchestrator) -> None:
        self.settings = settings
        self.store = store
        self.orchestrator = orchestrator
        self._background: set[asyncio.Task[None]] = set()
        # client key (IP bucket) → (session_hash, scenario_id) of its active-or-queued run.
        # In memory only: never persisted or logged.
        self._clients: dict[str, tuple[str, str]] = {}
        self.live_tavily: TavilySource | None = None  # tests inject a fake live Tavily
        orchestrator.add_listener(self._on_edit_done)

    # -- catalog -------------------------------------------------------------------------------
    def scenarios(self) -> list[ScenarioInfo]:
        return [s.info for s in load_scenarios(self.settings.ratchet_scenarios_dir).values()]

    # -- start ---------------------------------------------------------------------------------
    async def start(
        self, scenario_id: str, session_hash: str, *, client_key: str | None = None
    ) -> SimulateStart:
        """Start (or queue) ``scenario_id`` for ``session_hash``; NotFoundError if unknown.

        ``client_key`` (the API's IP bucket) allows at most one active-or-queued run per
        client; a full queue (``ratchet_simulate_max_queue``) is 429 ``simulate_busy``.
        """
        scenario = get_scenario(self.settings.ratchet_scenarios_dir, scenario_id)
        if not self.settings.ratchet_simulate_enabled:
            raise ForbiddenError("Simulate is turned off on this host", code="simulate_disabled")
        async with _guard():
            current = self._row(session_hash, scenario_id)
            if current is not None and current.status in ("running", "queued"):
                return self._view(current)
            self._check_client(client_key)
            if client_key is not None:
                self._clients[client_key] = (session_hash, scenario_id)
            if self.settings.ratchet_simulate_mode == "replayed":
                from ratchet.orchestrator.recorded import start_recorded

                started = await start_recorded(self.settings, self.store, scenario_id, session_hash)
                self._keep(started.task)
                return started.view
            running = self.store.sessions(status="running")
            if len(running) >= self.settings.ratchet_simulate_max_sessions:
                if len(self.store.sessions(status="queued")) >= self._max_queue():
                    self._clients.pop(client_key or "", None)
                    raise SimulateBusy(_BUSY_QUEUE, context={"reason": "queue_full"})
                return await asyncio.to_thread(self._queue, scenario_id, session_hash)
            return await self._launch(scenario, session_hash)

    def _max_queue(self) -> int:
        return max(0, self.settings.ratchet_simulate_max_queue)

    def _check_client(self, client_key: str | None) -> None:
        """At most one active-or-queued run per client bucket; stale entries are dropped."""
        if client_key is None:
            return
        held = self._clients.get(client_key)
        if held is None:
            return
        if self._active(self._row(*held)):
            raise SimulateBusy(_BUSY_CLIENT, context={"reason": "client_busy"})
        del self._clients[client_key]

    def _active(self, row: SessionRow | None) -> bool:
        if row is None or row.status not in ("running", "queued"):
            return False
        return row.status == "queued" or row.edit_id is None or self._in_flight(row.edit_id)

    def _keep(self, task: asyncio.Task[None]) -> None:
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def drain(self) -> None:
        """Wait for background work (queued-session promotion) to finish: shutdown, tests."""
        while self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    def _row(self, session_hash: str, scenario_id: str) -> SessionRow | None:
        for row in self.store.sessions():
            if row.session_hash == session_hash and row.scenario == scenario_id:
                return row
        return None

    def _view(self, row: SessionRow) -> SimulateStart:
        if row.status == "queued":
            queued = self.store.sessions(status="queued")
            position = next(
                (i for i, r in enumerate(queued, 1) if r.session_hash == row.session_hash), 1
            )
            return SimulateStart(
                session_hash=row.session_hash,
                scenario_id=row.scenario,
                edit_id=None,
                verdict_id=None,
                status="queued",
                queue_position=position,
                eta_s=position * ETA_PER_RUN_S,
            )
        status: Literal["running", "done"] = "running" if row.status == "running" else "done"
        return SimulateStart(
            session_hash=row.session_hash,
            scenario_id=row.scenario,
            edit_id=row.edit_id,
            verdict_id=row.verdict_id,
            status=status,
        )

    def _queue(self, scenario_id: str, session_hash: str) -> SimulateStart:
        now = clock.now()
        reset_at = now + timedelta(seconds=self.settings.ratchet_simulate_reset_s)
        row = SessionRow(session_hash, scenario_id, None, None, "queued", now, reset_at)
        self.store.upsert_session(row)
        view = self._view(row)
        payload: dict[str, object] = {
            "scenario": scenario_id,
            "session_hash": session_hash,
            "status": "queued",
        }
        self.store.emit("simulate.started", scenario_id, payload, session_hash=session_hash)
        return view

    # -- launch --------------------------------------------------------------------------------
    def _run_dir(self, session_hash: str, scenario_id: str) -> Path:
        return session_root(self.store, session_hash) / scenario_id

    def _fresh_repo(self, run_dir: Path) -> SkillsRepo:
        source = operator_repo(self.settings)
        if not (source.main_dir / ".git").exists():
            raise ConflictError(
                "the demo is not seeded; run `ratchet seed-demo`", code="not_seeded"
            )
        shutil.rmtree(run_dir, ignore_errors=True)
        return source.clone_to(run_dir / "skills")

    def _goldens(self) -> list[Trace]:
        rows = self.store.list_traces(kind="golden", limit=1_000_000)
        return [self.store.load_trace(r.run_id) for r in rows if r.world_dependent]

    def _world_ref(self, scenario: Scenario, world: WorldChoice | None) -> str | None:
        if world is None:
            return None
        built = overlay.build(
            self.store,
            self.settings,
            world.overlay_name,
            world.override,
            self._goldens(),
            base_world=self.settings.ratchet_scenarios_dir / "world",
        )
        return str(built)

    async def _world(self, scenario: Scenario) -> WorldChoice | None:
        if scenario.world_dir is None:
            return None
        return await choose(
            self.settings,
            self.store,
            scenario.scenario_id,
            scenario.world_dir,
            scenario.tavily_dir,
            scenario.live_world,
            tavily=self.live_tavily,
        )

    def _seed(
        self, scenario: Scenario, session_hash: str, world: WorldChoice | None
    ) -> SimulateStart:
        run_dir = self._run_dir(session_hash, scenario.scenario_id)
        repo = self._fresh_repo(run_dir)
        message = f"ratchet: seeded scenario {scenario.scenario_id}"
        head = repo.apply_files(scenario.edit_files(), message=message, author="seeded")
        edit, diff = build_edit(repo, commit=head, author="seeded", session_hash=session_hash)
        tavily_dir = world.tavily_dir if world is not None else scenario.tavily_dir
        options = EditOptions(
            scenario_id=scenario.scenario_id,
            repo_dir=str(repo.main_dir),
            world_ref=self._world_ref(scenario, world),
            tavily_dir=str(tavily_dir) if tavily_dir else None,
            tavily_fixtures_only=world.fixtures_only if world is not None else False,
        )
        save_options(self.store, edit.edit_id, options)
        verdict_id = verdict_id_for(edit.edit_id)
        now = clock.now()
        reset_at = now + timedelta(seconds=self.settings.ratchet_simulate_reset_s)
        row = SessionRow(
            session_hash, scenario.scenario_id, edit.edit_id, verdict_id, "running", now, reset_at
        )
        self.store.upsert_session(row)
        self.orchestrator.submit_edit(edit, diff)
        payload: dict[str, object] = {
            "scenario": scenario.scenario_id,
            "session_hash": session_hash,
            "verdict_id": verdict_id,
            "edit_id": edit.edit_id,
            "status": "running",
        }
        self.store.emit("simulate.started", verdict_id, payload, session_hash=session_hash)
        _log.info("simulate.started", session_hash=session_hash, edit_id=edit.edit_id)
        return self._view(row)

    async def _launch(self, scenario: Scenario, session_hash: str) -> SimulateStart:
        world = await self._world(scenario)
        return await asyncio.to_thread(self._seed, scenario, session_hash, world)

    # -- queue ---------------------------------------------------------------------------------
    def _on_edit_done(self, edit_id: str, state: str) -> None:
        if self.store.sessions(status="queued"):
            self._keep(asyncio.get_running_loop().create_task(self._promote()))

    async def _promote(self) -> None:
        async with _guard():
            free = self.settings.ratchet_simulate_max_sessions - len(
                self.store.sessions(status="running")
            )
            for row in self.store.sessions(status="queued")[: max(free, 0)]:
                scenario = get_scenario(self.settings.ratchet_scenarios_dir, row.scenario)
                await self._launch(scenario, row.session_hash)

    # -- reset ---------------------------------------------------------------------------------
    def _expire(self) -> int:
        now, count = clock.now(), 0
        for row in self.store.sessions():
            if row.status in ("reset", "queued") or row.reset_at > now:
                continue
            if row.edit_id is not None and self._in_flight(row.edit_id):
                continue
            shutil.rmtree(self._run_dir(row.session_hash, row.scenario), ignore_errors=True)
            root = session_root(self.store, row.session_hash)
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
            self.store.upsert_session(replace(row, status="reset"))
            payload = {"scenario": row.scenario, "session_hash": row.session_hash}
            self.store.emit(
                "simulate.reset",
                row.verdict_id or row.scenario,
                {**payload, "verdict_id": row.verdict_id},
                session_hash=row.session_hash,
            )
            count += 1
        return count

    def _in_flight(self, edit_id: str) -> bool:
        try:
            return self.store.edit_state(edit_id).state not in TERMINAL
        except NotFoundError:
            return False

    async def reset_expired(self) -> int:
        """Delete expired session repos and mark their sessions ``reset``; returns the count."""
        async with _guard():
            return await asyncio.to_thread(self._expire)
