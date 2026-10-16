"""``GET /events`` (feed and polling fallback) and ``GET /events/stream`` (SSE).

Both are session-filtered by the ``ratchet_session`` cookie: the caller's own rows plus
global rows (``events.session_hash`` is null), never another session's.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from sse_starlette import EventSourceResponse

from ratchet.dashboard_api.deps import ServicesDep, SessionDep, client_key
from ratchet.dashboard_api.errors import BadRequest
from ratchet.dashboard_api.queries import MAX_LIMIT, event_page
from ratchet.dashboard_api.sse import event_frames, resume_point, stream_response
from ratchet.dashboard_api.views import Page
from ratchet.models import Event

__all__ = ["router", "parse_kinds"]

router = APIRouter(tags=["events"])
_KIND = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def parse_kinds(raw: str | None) -> list[str]:
    kinds = [k.strip() for k in (raw or "").split(",") if k.strip()]
    if len(kinds) > 30 or any(not _KIND.fullmatch(k) for k in kinds):
        raise BadRequest("The kinds filter is not valid.")
    return kinds


@router.get("/events", response_model=Page[Event])
async def list_events(
    services: ServicesDep,
    session: SessionDep,
    since: Annotated[int | None, Query(ge=0)] = None,
    kinds: Annotated[str | None, Query(max_length=1000)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = 50,
    cursor: str | None = None,
) -> Page[Event]:
    """Newest first; with ``since``, oldest first after that id (the polling fallback)."""
    rows, nxt = await asyncio.to_thread(
        lambda: event_page(
            services.store,
            session_hash=session.hash,
            since=since,
            kinds=parse_kinds(kinds),
            cursor=cursor,
            limit=limit,
        )
    )
    return Page[Event](items=rows, next_cursor=nxt)


@router.get(
    "/events/stream",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "SSE stream"}},
)
async def stream_events(
    request: Request,
    services: ServicesDep,
    session: SessionDep,
    since: Annotated[int | None, Query(ge=0)] = None,
    kinds: Annotated[str | None, Query(max_length=1000)] = None,
    last_event_id: Annotated[str | None, Header(max_length=32)] = None,
) -> EventSourceResponse:
    """``id:``/``event:``/``data:`` frames; heartbeat comment every 15 s; resumes after
    ``Last-Event-ID`` (or ``since``), else streams only new events. At most
    ``ratchet_sse_max_per_client`` streams per client and ``ratchet_sse_max_streams`` in
    total; over that, 429 ``rate_limited``."""
    config = services.options.stream
    wanted = parse_kinds(kinds)
    client = client_key(request)
    services.stream_slots.acquire(client)
    released = False

    def release() -> None:
        nonlocal released
        if not released:
            released = True
            services.stream_slots.release(client)

    frames = event_frames(
        services.store,
        session_hash=session.hash,
        after=resume_point(since, last_event_id),
        kinds=wanted,
        config=config,
        hub=services.hub(),
        on_close=release,
    )
    return stream_response(frames, config, on_close=release)
