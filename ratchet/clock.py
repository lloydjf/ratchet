"""The only clock in Ratchet.

``datetime.now()`` is banned by a ruff rule everywhere else. Tests and replays freeze
time with :func:`frozen`.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator
from contextvars import ContextVar
from datetime import UTC, datetime

__all__ = ["now", "frozen", "monotonic_ms", "iso", "parse"]

_frozen: ContextVar[datetime | None] = ContextVar("ratchet_frozen_time", default=None)


def now() -> datetime:
    """Current time, UTC, timezone-aware. Frozen inside :func:`frozen`."""
    value = _frozen.get()
    return value if value is not None else datetime.now(UTC)


@contextlib.contextmanager
def frozen(at: datetime) -> Iterator[None]:
    if at.tzinfo is None:
        raise ValueError("frozen time must be timezone-aware")
    token = _frozen.set(at.astimezone(UTC))
    try:
        yield
    finally:
        _frozen.reset(token)


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def iso(value: datetime) -> str:
    """ISO 8601 with offset; naive datetimes are rejected rather than guessed."""
    if value.tzinfo is None:
        raise ValueError("refusing to serialize a naive datetime")
    return value.astimezone(UTC).isoformat()


def parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp without offset: {value!r}")
    return parsed.astimezone(UTC)
