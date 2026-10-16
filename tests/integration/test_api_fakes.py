"""Fakes and fixtures for the dashboard API tests (imported by the other test_api_* modules).

The fakes implement the pinned orchestrator interface (orchestrator-api-cli.md §11) so the
API is tested without the real orchestrator; test_api_real_orchestrator.py covers the real one.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.dashboard_api.app import create_app
from ratchet.dashboard_api.deps import ApiOptions
from ratchet.dashboard_api.ports import Backend, OrchestratorLike, SimulateLike
from ratchet.dashboard_api.security import TokenBucket
from ratchet.dashboard_api.sse import StreamConfig
from ratchet.dashboard_api.views import Progress, StageView
from ratchet.errors import ForbiddenError, NotFoundError
from ratchet.models import (
    Decision,
    SkillEdit,
    Trace,
    Verdict,
)
from ratchet.store import Store

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_api_factories import NOW, seed_running  # noqa: E402

__all__ = [
    "ApiEnv",
    "FakeBackend",
    "FakeOrchestrator",
    "FakeSimulateService",
    "make_env",
    "api_reset",
]


@pytest.fixture(autouse=True)
def api_reset() -> Iterator[None]:
    yield
    reset_settings()


# -- orchestrator fakes (§11) ------------------------------------------------------------
class ScenarioInfo(BaseModel):
    scenario_id: str
    skill: str
    title: str
    learned: str
    catches: str
    expected_decision: Decision


class SimulateStart(BaseModel):
    session_hash: str
    scenario_id: str
    edit_id: str | None
    verdict_id: str | None
    status: Literal["running", "queued", "done"]
    queue_position: int | None = None
    eta_s: int | None = None


class FakeOrchestrator:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.submitted: list[str] = []
        self.replayed: list[str] = []

    def submit_edit(self, edit: SkillEdit, diff: str) -> None:
        self.store.save_edit(edit, diff)
        self.submitted.append(edit.edit_id)

    async def replay(self, edit_id: str) -> None:
        self.replayed.append(edit_id)
        self.store.set_edit_state(edit_id, "selecting")


class FakeSimulateService:
    SCENARIOS = ("1", "2", "3")

    def __init__(self, store: Store) -> None:
        self.store = store
        self.started: list[tuple[str, str]] = []
        self.client_keys: list[str | None] = []

    def scenarios(self) -> list[ScenarioInfo]:
        return [
            ScenarioInfo(
                scenario_id=k,
                skill="github-triage",
                title=f"Scenario {k}",
                learned="Closes stale issues",
                catches="New close_issue calls",
                expected_decision="BLOCK",
            )
            for k in self.SCENARIOS
        ]

    async def start(
        self, scenario_id: str, session_hash: str, *, client_key: str | None = None
    ) -> SimulateStart:
        if scenario_id not in self.SCENARIOS:
            raise NotFoundError(f"scenario {scenario_id} not found")
        hexid = f"s{len(self.started)}{scenario_id}{session_hash[:6]}"
        self.client_keys.append(client_key)
        self.started.append((scenario_id, session_hash))
        seed_running(self.store, hexid, session_hash=session_hash)
        self.store.emit(
            "simulate.started",
            f"edit_{hexid}",
            {"scenario": scenario_id},
            session_hash=session_hash,
        )
        return SimulateStart(
            session_hash=session_hash,
            scenario_id=scenario_id,
            edit_id=f"edit_{hexid}",
            verdict_id=f"verdict_{hexid}",
            status="running",
        )


class FakeBackend(Backend):
    """Implements the §11 progress/actions/bless/digest functions over the store."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.digest: BaseModel | None = None

    def progress_for(self, store: Store, edit_id: str) -> BaseModel | None:
        state = store.edit_state(edit_id).state
        if state == "done":
            return None
        return Progress(
            stage=state,
            stage_label="Replaying on Nebius Jobs (3/14)",
            k=3,
            n=14,
            elapsed_s=12.5,
            usd_by_tier={"nano": 0.001},
            stalled=False,
            stages=[
                StageView(key="detected", label="Detected edit", status="done"),
                StageView(key=state, label="Replaying", status="active", detail="3/14"),
            ],
        )

    async def _act(self, store: Store, verdict_id: str, actor: str, which: str) -> Verdict:
        self.calls.append((which, verdict_id, actor))
        verdict = store.load_verdict(verdict_id)
        update: dict[str, object] = (
            {"action": "reverted", "rolled_back_by": actor, "rolled_back_at": NOW}
            if which == "rollback"
            else {"action": "merged", "approved_at": NOW}
        )
        verdict = verdict.model_copy(update=update)
        store.save_verdict(verdict)
        return verdict

    async def rollback(
        self, store: Store, settings: Settings, verdict_id: str, *, actor: str
    ) -> Verdict:
        return await self._act(store, verdict_id, actor, "rollback")

    async def approve(
        self, store: Store, settings: Settings, verdict_id: str, *, actor: str
    ) -> Verdict:
        return await self._act(store, verdict_id, actor, "approve")

    async def bless(self, store: Store, settings: Settings, run_id: str) -> Trace:
        if settings.ratchet_public_mode:
            raise ForbiddenError("no", code="not_public")
        self.calls.append(("bless", run_id))
        trace = store.load_trace(run_id).model_copy(update={"kind": "golden"})
        store.save_trace(trace)
        return trace

    async def rebless(
        self,
        store: Store,
        settings: Settings,
        golden_run_id: str,
        replay_run_id: str,
        *,
        session_hash: str | None = None,
    ) -> tuple[str, str]:
        self.calls.append(("rebless", golden_run_id, replay_run_id, session_hash))
        return f"{replay_run_id}_golden", golden_run_id

    def unbless(self, store: Store, settings: Settings, run_id: str) -> Trace:
        self.calls.append(("unbless", run_id))
        return store.load_trace(run_id).model_copy(update={"kind": "live"})

    def set_criteria(
        self, store: Store, settings: Settings, run_id: str, criteria: list[str]
    ) -> Trace:
        self.calls.append(("criteria", run_id, tuple(criteria)))
        return store.load_trace(run_id).model_copy(update={"success_criteria": criteria})

    def latest_digest(self, store: Store) -> BaseModel | None:
        return self.digest

    def make_simulate(
        self, settings: Settings, store: Store, orchestrator: OrchestratorLike
    ) -> SimulateLike:
        return FakeSimulateService(store)


# -- environment -------------------------------------------------------------------------
@dataclass
class ApiEnv:
    settings: Settings
    store: Store
    app: FastAPI
    backend: FakeBackend
    simulate: FakeSimulateService
    orchestrator: FakeOrchestrator
    tmp_path: Path
    clients: list[httpx.AsyncClient] = field(default_factory=list)

    def client(self, ip: str = "127.0.0.1") -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=(ip, 50000))
        return httpx.AsyncClient(transport=transport, base_url="http://testserver")


def make_env(
    tmp_path: Path,
    *,
    public: bool = False,
    simulate_enabled: bool = True,
    stream: StreamConfig | None = None,
    limiter: TokenBucket | None = None,
    with_orchestrator: bool = True,
    **overrides: object,
) -> ApiEnv:
    values: dict[str, object] = {
        "ratchet_data_dir": tmp_path / "data",
        "ratchet_dashboard_dist": tmp_path / "dist",
        "ratchet_public_mode": public,
        "ratchet_simulate_enabled": simulate_enabled,
        "ratchet_simulate_mode": "live",
        "ratchet_upstream": "sim",
        "ratchet_public_url": "http://testserver",
        "ratchet_action_token_secret": "test-secret",
        "ratchet_video_url": "",
        "ratchet_log_level": "WARNING",
    }
    values.update(overrides)
    settings = configure(Settings.model_validate(values))
    store = Store.open(tmp_path / "data")
    backend = FakeBackend()
    simulate = FakeSimulateService(store)
    orchestrator = FakeOrchestrator(store)
    options = ApiOptions(
        backend=backend,
        simulate=simulate,
        stream=stream or StreamConfig(poll_s=0.01, heartbeat_s=15.0, max_duration_s=0.3),
        limiter=limiter,
    )
    app = create_app(settings, store, orchestrator if with_orchestrator else None, options=options)
    return ApiEnv(settings, store, app, backend, simulate, orchestrator, tmp_path)
