"""``GET /scenarios`` and ``POST /simulate/{scenario_id}`` (CONTRACTS §7)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from ratchet.dashboard_api.builders import check_id
from ratchet.dashboard_api.deps import NewSessionDep, ServicesDep, action_guard, client_key
from ratchet.dashboard_api.errors import NotReady
from ratchet.dashboard_api.views import ScenarioView, SimulateView
from ratchet.errors import ForbiddenError, NotFoundError

__all__ = ["router"]

router = APIRouter(tags=["simulate"])


@router.get("/scenarios", response_model=list[ScenarioView])
async def list_scenarios(services: ServicesDep) -> list[ScenarioView]:
    """The seeded scenarios; empty when Simulate is disabled or not wired."""
    simulate = services.simulate()
    if simulate is None:
        return []
    infos = await asyncio.to_thread(simulate.scenarios)
    return [ScenarioView.model_validate(info.model_dump(mode="json")) for info in infos]


async def _require_enabled(services: ServicesDep) -> None:
    if not services.simulate_enabled:
        raise ForbiddenError("Simulate is turned off on this host.", code="simulate_disabled")


@router.post(
    "/simulate/{scenario_id}",
    response_model=SimulateView,
    dependencies=[Depends(_require_enabled), Depends(action_guard())],
)
async def start_simulate(
    scenario_id: str, request: Request, services: ServicesDep, session: NewSessionDep
) -> SimulateView:
    """Apply the scenario's seeded edit in this session's copy of the skills repo. The
    response carries the pre-allocated ``verdict_id`` so the client can open it at once."""
    check_id(scenario_id, "scenario")
    simulate = services.simulate()
    if simulate is None:
        raise NotReady("Simulate is not available right now.")
    if session.hash is None:  # pragma: no cover - ensure_session always sets one
        raise NotFoundError("no session")
    started = await simulate.start(scenario_id, session.hash, client_key=client_key(request))
    return SimulateView.model_validate(started.model_dump(mode="json"))
