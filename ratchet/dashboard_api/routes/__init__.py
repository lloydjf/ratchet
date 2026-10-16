"""Route modules. :func:`api_router` composes everything served under ``/api/v1``."""

from __future__ import annotations

from fastapi import APIRouter

__all__ = ["api_router"]


def api_router() -> APIRouter:
    from ratchet.dashboard_api.routes import (
        costs,
        demo,
        digest,
        edits,
        events,
        meta,
        metrics,
        runs,
        simulate,
        traces,
        verdicts,
    )

    router = APIRouter()
    modules = (
        meta,
        simulate,
        verdicts,
        edits,
        traces,
        runs,
        costs,
        digest,
        events,
        demo,
        metrics,
    )
    for module in modules:
        router.include_router(module.router)
    return router
