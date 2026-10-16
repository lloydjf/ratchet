"""The API's seam to the orchestrator (orchestrator-api-cli.md §11).

The orchestrator modules are imported lazily, inside each call, so the API imports and
serves read-only routes even before ``ratchet/orchestrator/{progress,actions,bless,digest,
simulate,ids}.py`` exist. Tests subclass :class:`Backend` (or pass fakes for the
orchestrator and Simulate service) instead of monkeypatching modules that may be missing.
"""

from __future__ import annotations

import importlib
from collections.abc import Sequence
from types import ModuleType
from typing import Protocol

from pydantic import BaseModel

from ratchet.config.settings import Settings
from ratchet.dashboard_api.errors import NotReady
from ratchet.models import SkillEdit, Trace, Verdict
from ratchet.store import Store

__all__ = ["Backend", "OrchestratorLike", "SimulateLike", "orchestrator_module"]


class OrchestratorLike(Protocol):
    """The part of ``service.Orchestrator`` the API calls."""

    def submit_edit(self, edit: SkillEdit, diff: str) -> None: ...

    async def replay(self, edit_id: str) -> None: ...


class SimulateLike(Protocol):
    """The part of ``simulate.SimulateService`` the API calls."""

    def scenarios(self) -> Sequence[BaseModel]: ...

    async def start(
        self, scenario_id: str, session_hash: str, *, client_key: str | None = None
    ) -> BaseModel: ...


def orchestrator_module(name: str) -> ModuleType:
    """Import ``ratchet.orchestrator.<name>``; a missing module is a 503 ``not_ready``."""
    full = f"ratchet.orchestrator.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as exc:
        if exc.name is not None and (exc.name == full or full.startswith(exc.name + ".")):
            raise NotReady(
                "This part of Ratchet is not installed yet.", context={"module": name}
            ) from exc
        raise


class Backend:
    """Default implementation: the real orchestrator modules, imported on first use."""

    # -- progress.py ------------------------------------------------------------------------
    def progress_for(self, store: Store, edit_id: str) -> BaseModel | None:
        result: BaseModel | None = orchestrator_module("progress").progress_for(store, edit_id)
        return result

    # -- actions.py -------------------------------------------------------------------------
    async def rollback(
        self, store: Store, settings: Settings, verdict_id: str, *, actor: str
    ) -> Verdict:
        module = orchestrator_module("actions")
        result: Verdict = await module.rollback(store, settings, verdict_id, actor=actor)
        return result

    async def approve(
        self, store: Store, settings: Settings, verdict_id: str, *, actor: str
    ) -> Verdict:
        module = orchestrator_module("actions")
        result: Verdict = await module.approve(store, settings, verdict_id, actor=actor)
        return result

    # -- bless.py ---------------------------------------------------------------------------
    async def bless(self, store: Store, settings: Settings, run_id: str) -> Trace:
        result: Trace = await orchestrator_module("bless").bless(store, settings, run_id)
        return result

    async def rebless(
        self,
        store: Store,
        settings: Settings,
        golden_run_id: str,
        replay_run_id: str,
        *,
        session_hash: str | None = None,
    ) -> tuple[str, str]:
        module = orchestrator_module("bless")
        result: tuple[str, str] = await module.rebless(
            store, settings, golden_run_id, replay_run_id, session_hash=session_hash
        )
        return result

    def unbless(self, store: Store, settings: Settings, run_id: str) -> Trace:
        result: Trace = orchestrator_module("bless").unbless(store, settings, run_id)
        return result

    def set_criteria(
        self, store: Store, settings: Settings, run_id: str, criteria: list[str]
    ) -> Trace:
        module = orchestrator_module("bless")
        result: Trace = module.set_criteria(store, settings, run_id, criteria)
        return result

    # -- digest.py --------------------------------------------------------------------------
    def latest_digest(self, store: Store) -> BaseModel | None:
        result: BaseModel | None = orchestrator_module("digest").latest(store)
        return result

    # -- simulate.py ------------------------------------------------------------------------
    def make_simulate(
        self, settings: Settings, store: Store, orchestrator: OrchestratorLike
    ) -> SimulateLike:
        service: SimulateLike = orchestrator_module("simulate").SimulateService(
            settings, store, orchestrator
        )
        return service

    # -- ids.py -----------------------------------------------------------------------------
    def verdict_id_for(self, edit_id: str) -> str:
        """§2: ``edit_<hex>`` → ``verdict_<hex>``; falls back to that rule if ids.py is absent."""
        try:
            result: str = orchestrator_module("ids").verdict_id_for(edit_id)
        except NotReady:
            return "verdict_" + edit_id.removeprefix("edit_")
        return result
