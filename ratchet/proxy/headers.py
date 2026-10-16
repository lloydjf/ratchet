"""Request tagging: ``X-Ratchet-*`` headers in, upstream headers out.

Pure functions over header mappings so the routing rules are testable without a server.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from ratchet.errors import RatchetError
from ratchet.llm.client import HEADER
from ratchet.models import TIERS, Purpose, Tier
from ratchet.util import new_id

__all__ = [
    "BadRequest",
    "CallContext",
    "PURPOSES",
    "parse_context",
    "parse_tier",
    "parse_purpose",
    "valid_run_id",
    "upstream_headers",
    "passthrough_response_headers",
    "request_id_from",
]

PURPOSES: tuple[Purpose, ...] = (
    "agent",
    "screen",
    "check",
    "adjudicate",
    "extract",
    "narrative",
    "bench",
    "embed",
)
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_PASSTHROUGH_PREFIXES = ("x-ratelimit-",)
_PASSTHROUGH_NAMES = frozenset({"retry-after"})


class BadRequest(RatchetError):
    """The caller sent something the proxy cannot route (bad tier, purpose, run id, body)."""

    code = "bad_request"
    http_status = 400


@dataclass(frozen=True)
class CallContext:
    """Everything the proxy knows about one model call before it reads the body."""

    run_id: str
    run_id_generated: bool
    tier_header: Tier | None
    purpose: Purpose
    edit_id: str | None
    session_hash: str | None
    prompt_name: str | None
    thinking: bool | None
    reasoning_budget: int | None


def _get(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    value = value.strip() if value is not None else None
    return value or None


def valid_run_id(value: str) -> bool:
    return bool(_RUN_ID.match(value)) and ".." not in value


def parse_tier(value: str | None) -> Tier | None:
    if value is None:
        return None
    for tier in TIERS:
        if tier == value.strip().lower():
            return tier
    return None


def parse_purpose(value: str | None) -> Purpose:
    if value is None:
        return "agent"
    for purpose in PURPOSES:
        if purpose == value.strip().lower():
            return purpose
    raise BadRequest(f"unknown purpose {value[:32]!r}", context={"header": HEADER.PURPOSE})


def _parse_thinking(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.lower()
    if lowered in {"on", "true", "1"}:
        return True
    if lowered in {"off", "false", "0"}:
        return False
    raise BadRequest("X-Ratchet-Thinking must be on or off", context={"header": HEADER.THINKING})


def _parse_budget(value: str | None) -> int | None:
    if value is None:
        return None
    if not value.isdigit():
        raise BadRequest(
            "X-Ratchet-Reasoning-Budget must be a non-negative integer",
            context={"header": HEADER.REASONING_BUDGET},
        )
    return int(value)


def _resolve_run_id(headers: Mapping[str, str], path_run_id: str | None) -> tuple[str, bool]:
    run_id = path_run_id or _get(headers, HEADER.RUN_ID)
    if run_id is None:
        return new_id("run"), True
    if not valid_run_id(run_id):
        raise BadRequest("invalid run id", context={"header": HEADER.RUN_ID})
    return run_id, False


def parse_context(headers: Mapping[str, str], path_run_id: str | None = None) -> CallContext:
    """Resolve run id (path form wins over the header; generated if absent) and tags."""
    run_id, generated = _resolve_run_id(headers, path_run_id)
    tier_raw = _get(headers, HEADER.TIER)
    tier = parse_tier(tier_raw)
    if tier_raw is not None and tier is None:
        raise BadRequest("X-Ratchet-Tier must be nano, super or ultra")
    return CallContext(
        run_id=run_id,
        run_id_generated=generated,
        tier_header=tier,
        purpose=parse_purpose(_get(headers, HEADER.PURPOSE)),
        edit_id=_get(headers, HEADER.EDIT_ID),
        session_hash=_get(headers, HEADER.SESSION),
        prompt_name=_get(headers, HEADER.PROMPT),
        thinking=_parse_thinking(_get(headers, HEADER.THINKING)),
        reasoning_budget=_parse_budget(_get(headers, HEADER.REASONING_BUDGET)),
    )


def upstream_headers(
    ctx: CallContext, *, api_key: str | None, simulator: bool, stream: bool
) -> dict[str, str]:
    """Headers sent upstream. Nothing from the caller is copied (its key is a dummy, and
    ``X-Ratchet-*`` is internal), except the prompt/purpose tags the simulator dispatches on."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream" if stream else "application/json",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if simulator:
        headers[HEADER.PURPOSE] = ctx.purpose
        if ctx.prompt_name:
            headers[HEADER.PROMPT] = ctx.prompt_name
    return headers


def passthrough_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Upstream rate-limit headers the harness may act on (Token Factory ``x-ratelimit-*``)."""
    out: dict[str, str] = {}
    for name, value in headers.items():
        lowered = name.lower()
        if lowered in _PASSTHROUGH_NAMES or lowered.startswith(_PASSTHROUGH_PREFIXES):
            out[lowered] = value
    return out


def request_id_from(headers: Mapping[str, str]) -> str:
    value = _get(headers, "X-Request-Id")
    if value is not None and _REQUEST_ID.match(value):
        return value
    return new_id("req")
