"""Shared CLI plumbing: settings, store, async runner, tables, error exits."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Coroutine, Sequence
from datetime import timedelta
from typing import TypeVar

import typer

from ratchet.config.settings import Settings, get_settings
from ratchet.errors import RatchetError
from ratchet.store import Store

__all__ = ["settings", "store", "run", "table", "fail", "parse_since", "short"]

T = TypeVar("T")
_SINCE = re.compile(r"^(\d+)([dhm])$")


def settings() -> Settings:
    return get_settings()


def store(cfg: Settings | None = None) -> Store:
    return Store.open((cfg or settings()).ratchet_data_dir)


def fail(message: str, code: int = 1) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def run(coro: Coroutine[object, object, T]) -> T:
    """Run ``coro``; a ``RatchetError`` becomes ``error <code>: <message>`` and exit 1."""
    try:
        return asyncio.run(coro)
    except RatchetError as exc:
        raise fail(f"error {exc.code}: {exc.message}") from exc


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    cells = [[str(h) for h in headers], *[[str(c) for c in row] for row in rows]]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)) for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(line.rstrip() for line in lines)


def parse_since(value: str) -> timedelta:
    match = _SINCE.match(value.strip())
    if match is None:
        raise typer.BadParameter("use a number and d, h or m, e.g. 7d")
    amount, unit = int(match.group(1)), match.group(2)
    return {"d": timedelta(days=amount), "h": timedelta(hours=amount)}.get(
        unit, timedelta(minutes=amount)
    )


def short(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
