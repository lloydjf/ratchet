"""The Orchestrator: FIFO edit queue, bounded concurrency, resume, watcher, timers.

- ``submit_edit`` (thread-safe; the watcher calls it from its own thread) saves the edit,
  emits ``edit.detected`` and enqueues it.
- At most ``ratchet_max_edits_in_flight`` edits run at once, FIFO; an edit whose affected
  skills overlap an edit in flight *in the same repository* waits.
  Simulate sessions have their own repositories, so they never wait on each other.
- ``start`` resumes every edit left in a non-terminal state (restartability) and
  starts the watcher, the Simulate auto-reset loop (30 s) and the weekly digest check.
- Stall detection and the local retry live in the collecting handler (``replaying``).
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import Callable
from datetime import timedelta

import httpx

from ratchet import clock
from ratchet.config.settings import Settings, get_settings
from ratchet.errors import ConflictError
from ratchet.judge.cascade import CascadeConfig
from ratchet.log import get_logger
from ratchet.models import SkillEdit
from ratchet.orchestrator.context import (
    InProcessProxy,
    Services,
    TavilyFactory,
    default_runner,
    load_options,
    make_embedder,
    operator_repo,
    retry_runner,
    save_options,
)
from ratchet.orchestrator.embed import Embedder
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.orchestrator.journal import STATES, TERMINAL
from ratchet.orchestrator.pipeline import run_edit
from ratchet.replay.jobs import JobRunner
from ratchet.store import Store
from ratchet.watcher.service import Watcher

__all__ = ["Orchestrator", "run_forever", "EditListener"]

EditListener = Callable[[str, str], None]  # (edit_id, final state)
_RESUMABLE = tuple(s for s in STATES if s not in TERMINAL)
_DIGEST_CHECK_S = 3600.0
_log = get_logger("orchestrator")


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        runner: JobRunner | None = None,
        retry: JobRunner | None = None,
        judge_http: httpx.AsyncClient | None = None,
        notify_http: httpx.AsyncClient | None = None,
        embedder: Embedder | None = None,
        cascade: CascadeConfig | None = None,
        tavily_factory: TavilyFactory | None = None,
        poll_s: float = 2.0,
        stall_s: float = 120.0,
        submit_backoff_s: tuple[float, ...] = (2.0, 4.0, 8.0),
        reset_interval_s: float = 30.0,
        digest_enabled: bool = True,
    ) -> None:
        self.settings = settings
        self.store = store
        self._proxy = None if judge_http is not None else InProcessProxy(settings, store)
        http = judge_http or (self._proxy.client if self._proxy is not None else None)
        assert http is not None
        self.services = Services(
            settings=settings,
            store=store,
            runner=runner or default_runner(settings, store),
            retry=retry or retry_runner(settings, store),
            judge_http=http,
            embedder=embedder or make_embedder(settings, http),
            notify_http=notify_http,
            cascade=cascade or CascadeConfig(),
            tavily_factory=tavily_factory,
            poll_s=poll_s,
            stall_s=stall_s,
            provision_s=settings.ratchet_jobs_provision_s,
            submit_backoff_s=submit_backoff_s,
        )
        self._reset_interval_s = reset_interval_s
        self._digest_enabled = digest_enabled
        self._lock = threading.Lock()
        self._incoming: list[str] = []
        self._queue: list[str] = []
        self._skills: dict[str, frozenset[tuple[str, str]]] = {}
        self._running: dict[str, asyncio.Task[str]] = {}
        self._listeners: list[EditListener] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._watcher: Watcher | None = None

    # -- intake ------------------------------------------------------------------------------
    def submit_edit(self, edit: SkillEdit, diff: str) -> None:
        """Save the edit, emit ``edit.detected`` and enqueue it (callable from any thread)."""
        saved = self.store.save_edit(edit, diff)
        payload: dict[str, object] = {
            "edit_id": saved.edit_id,
            "verdict_id": verdict_id_for(saved.edit_id),
            "commit": saved.commit,
            "affected_skills": list(saved.affected_skills),
            "author": saved.author,
            "summary": saved.summary,
        }
        self.store.emit("edit.detected", saved.edit_id, payload, session_hash=saved.session_hash)
        _log.info("orchestrator.edit.detected", edit_id=saved.edit_id, author=saved.author)
        self._enqueue(saved.edit_id)

    def _enqueue(self, edit_id: str) -> None:
        with self._lock:
            if edit_id not in self._incoming and edit_id not in self._queue:
                self._incoming.append(edit_id)
        self._poke()

    def _poke(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is not None and wake is not None and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)

    @property
    def running(self) -> bool:
        """True between ``start`` and ``stop`` (the dashboard's /healthz probe)."""
        return self._loop is not None

    def add_listener(self, listener: EditListener) -> None:
        self._listeners.append(listener)

    # -- scheduling ----------------------------------------------------------------------------
    def _skill_key(self, edit_id: str) -> frozenset[tuple[str, str]]:
        key = self._skills.get(edit_id)
        if key is None:
            edit = self.store.load_edit(edit_id)
            repo = load_options(self.store, edit_id).repo_dir or ""
            key = frozenset((repo, skill) for skill in edit.affected_skills)
            self._skills[edit_id] = key
        return key

    def _pump(self) -> None:
        with self._lock:
            self._queue.extend(e for e in self._incoming if e not in self._queue)
            self._incoming.clear()
            queue = list(self._queue)
        limit = max(self.settings.ratchet_max_edits_in_flight, 1)
        for edit_id in queue:
            if len(self._running) >= limit:
                break
            busy = (
                set().union(*(self._skill_key(e) for e in self._running))
                if self._running
                else set()
            )
            if busy & self._skill_key(edit_id):
                continue
            with self._lock:
                self._queue.remove(edit_id)
            self._running[edit_id] = asyncio.create_task(self._run(edit_id))

    async def _run(self, edit_id: str) -> str:
        state = "failed_infra"
        try:
            state = await run_edit(self.services, edit_id)
            return state
        finally:
            self._running.pop(edit_id, None)
            self._skills.pop(edit_id, None)
            for listener in list(self._listeners):
                try:
                    listener(edit_id, state)
                except Exception as exc:  # noqa: BLE001 - a listener never stops the queue
                    _log.error("orchestrator.listener_failed", error_type=type(exc).__name__)
            self._poke()

    async def _scheduler(self) -> None:
        assert self._wake is not None
        while True:
            self._wake.clear()
            self._pump()
            await self._wake.wait()

    def _is_idle(self) -> bool:
        with self._lock:
            return not self._incoming and not self._queue and not self._running

    async def wait_idle(self, timeout_s: float = 300) -> None:
        """Return once nothing is queued or running (tests, ``simulate --headless``)."""
        async with asyncio.timeout(timeout_s):
            while not self._is_idle():
                await asyncio.sleep(0.05)

    # -- lifecycle -----------------------------------------------------------------------------
    def _resume(self) -> list[str]:
        cleared = self.store.clear_action_locks()  # no action lock survives a restart
        if cleared:
            _log.warning("orchestrator.action_locks_cleared", n=cleared)
        resumed = [e.edit_id for e in self.store.edits_in_state(_RESUMABLE)]
        for e in self.store.edits_in_state(("failed_infra",)):
            if e.verdict_id is None:
                resumed.append(e.edit_id)
        return resumed

    async def start(self, *, watch: bool = True) -> None:
        """Resume non-terminal edits and start the scheduler, watcher and timers."""
        if self._loop is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        for edit_id in await asyncio.to_thread(self._resume):
            _log.info("orchestrator.edit.resumed", edit_id=edit_id)
            self._enqueue(edit_id)
        self._tasks.append(asyncio.create_task(self._scheduler()))
        if self.settings.ratchet_simulate_enabled:
            self._tasks.append(asyncio.create_task(self._reset_loop()))
        if self._digest_enabled:
            self._tasks.append(asyncio.create_task(self._digest_loop()))
        if watch:
            await asyncio.to_thread(self._start_watcher)
        self._poke()

    def _start_watcher(self) -> None:
        repo = operator_repo(self.settings)
        if not (repo.main_dir / ".git").exists():
            _log.warning("orchestrator.watcher.no_repo")
            return
        self._watcher = Watcher(repo, self.submit_edit)
        self._watcher.start()

    async def stop(self) -> None:
        if self._watcher is not None:
            await asyncio.to_thread(self._watcher.stop)
            self._watcher = None
        tasks = [*self._tasks, *self._running.values()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()
        self._running.clear()
        with self._lock:
            self._queue.clear()
            self._incoming.clear()
        self._loop, self._wake = None, None
        if self._proxy is not None:
            await self._proxy.aclose()
            self._proxy = None

    # -- actions -------------------------------------------------------------------------------
    async def replay(self, edit_id: str, *, runner: str | None = None) -> None:
        """``POST /edits/{id}/replay``: a fresh attempt from ``selecting``."""
        state = await asyncio.to_thread(self.store.edit_state, edit_id)
        with self._lock:
            busy = edit_id in self._queue or edit_id in self._incoming
        if busy or edit_id in self._running:
            raise ConflictError("this edit is already being checked", code="conflict")
        options = await asyncio.to_thread(load_options, self.store, edit_id)
        update: dict[str, object] = {"attempt": options.attempt + 1}
        if runner is not None:
            update["runner"] = runner
        await asyncio.to_thread(
            save_options, self.store, edit_id, options.model_copy(update=update)
        )
        await asyncio.to_thread(self.store.set_edit_state, edit_id, "selecting")
        edit = await asyncio.to_thread(self.store.load_edit, edit_id)
        payload: dict[str, object] = {
            "edit_id": edit_id,
            "verdict_id": verdict_id_for(edit_id),
            "from": state.state,
            "to": "selecting",
            "elapsed_ms": 0,
            "reason": "replay",
        }
        await asyncio.to_thread(
            self.store.emit, "orchestrator.state", edit_id, payload, session_hash=edit.session_hash
        )
        self._enqueue(edit_id)

    # -- timers --------------------------------------------------------------------------------
    async def _reset_loop(self) -> None:
        from ratchet.orchestrator.simulate import SimulateService

        service = SimulateService(self.settings, self.store, self)
        while True:
            await asyncio.sleep(self._reset_interval_s)
            try:
                await service.reset_expired()
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                _log.error("simulate.reset_failed", error_type=type(exc).__name__)

    def _has_verdicts(self) -> bool:
        return bool(self.store.list_verdicts(limit=1))

    async def _digest_loop(self) -> None:
        from ratchet.orchestrator import digest

        while True:
            await asyncio.sleep(_DIGEST_CHECK_S)
            try:
                last = await asyncio.to_thread(digest.latest, self.store)
                due = last is None or clock.now() - last.period_end >= timedelta(days=7)
                if due and await asyncio.to_thread(self._has_verdicts):
                    await digest.run_digest(self.store, self.settings)
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                _log.error("digest.schedule_failed", error_type=type(exc).__name__)


async def run_forever(settings: Settings | None = None) -> None:
    """CONTRACTS §1: the orchestrator + watcher loop as its own process."""
    settings = settings or get_settings()
    store = await asyncio.to_thread(Store.open, settings.ratchet_data_dir)
    orchestrator = Orchestrator(settings, store)
    await orchestrator.start(watch=True)
    try:
        await asyncio.Event().wait()
    finally:
        await orchestrator.stop()
