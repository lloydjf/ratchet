"""``GET /digest/latest`` (CONTRACTS §7). 404 with copy until a digest exists."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ratchet.dashboard_api.deps import ServicesDep
from ratchet.dashboard_api.views import DigestView
from ratchet.errors import NotFoundError

__all__ = ["router", "NO_DIGEST"]

router = APIRouter(tags=["digest"])

# Say what is missing and what happens next.
NO_DIGEST = "No digest yet. Ratchet writes one each week; run `ratchet digest run` to write it now."


@router.get("/digest/latest", response_model=DigestView)
async def latest_digest(services: ServicesDep) -> DigestView:
    digest = await asyncio.to_thread(services.backend.latest_digest, services.store)
    if digest is None:
        raise NotFoundError(NO_DIGEST)
    return DigestView.model_validate(digest.model_dump(mode="json"))
