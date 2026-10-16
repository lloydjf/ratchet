"""``POST /demo/events``: the anonymous demo funnel (ADR 0016). Public host only.

Privacy rules: no IP address, no user agent (only a device class), no cross-session id. The
session hash is the client's random per-tab value from the body, not the cookie. Only the
fields listed for each kind are stored; anything else is rejected with 422. Rows live
in ``demo_events`` for 30 days.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Callable

from fastapi import APIRouter, Request, Response

from ratchet.dashboard_api.deps import Services, ServicesDep, client_key
from ratchet.dashboard_api.errors import BadRequest, RateLimited
from ratchet.dashboard_api.views import DemoEventIn
from ratchet.errors import NotFoundError

__all__ = ["router", "ALLOWED_PROPS", "clean_payload"]

router = APIRouter(tags=["demo"])

_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_VERDICT = re.compile(r"^verdict_[A-Za-z0-9]{1,40}$")
_PRUNE_EVERY_S = 3600.0


def _slug(value: object) -> bool:
    return isinstance(value, str) and _SLUG.fullmatch(value) is not None


def _verdict(value: object) -> bool:
    return isinstance(value, str) and _VERDICT.fullmatch(value) is not None


def _seconds(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 86_400


def _one_of(*options: str) -> Callable[[object], bool]:
    return lambda value: isinstance(value, str) and value in options


ALLOWED_PROPS: dict[str, dict[str, Callable[[object], bool]]] = {
    "demo.landing_view": {},
    "demo.simulate_click": {"scenario": _slug},
    "demo.verdict_view": {"scenario": _slug, "verdict_id": _verdict, "seconds": _seconds},
    "demo.evidence_open": {"which": _one_of("pair", "diff", "drift", "cost", "trail")},
    "demo.action_click": {"which": _one_of("rollback", "approve", "rebless")},
    "demo.repo_click": {"target": _one_of("readme")},
    "demo.video_click": {},
}


def clean_payload(event: DemoEventIn) -> dict[str, object]:
    """The stored payload: allowlisted props, plus device and referrer on the landing view."""
    allowed = ALLOWED_PROPS[event.kind]
    payload: dict[str, object] = {}
    for key, value in event.props.items():
        check = allowed.get(key)
        if check is None or not check(value):
            raise BadRequest("The event has a field that is not allowed.", context={"field": key})
        payload[key] = value
    if event.kind == "demo.landing_view":
        payload["device"] = event.device or "desktop"
        payload["referrer"] = event.referrer or "none"
    return payload


def _store(services: Services, event: DemoEventIn, payload: dict[str, object]) -> None:
    store = services.store
    ref = str(payload.get("verdict_id") or payload.get("scenario") or "")
    store.emit(event.kind, ref, payload, session_hash=event.session_hash, table="demo_events")
    now = time.monotonic()
    if now - services.last_demo_prune >= _PRUNE_EVERY_S:
        services.last_demo_prune = now
        store.prune_demo_events(older_than_days=30)


@router.post("/demo/events", status_code=204, response_class=Response)
async def post_demo_event(event: DemoEventIn, request: Request, services: ServicesDep) -> Response:
    if not services.settings.ratchet_public_mode:
        raise NotFoundError("Demo analytics run on the public demo only.")
    retry_after = services.demo_limiter.take(client_key(request))
    if retry_after is not None:
        raise RateLimited(retry_after)
    payload = clean_payload(event)
    await asyncio.to_thread(_store, services, event, payload)
    return Response(status_code=204)
