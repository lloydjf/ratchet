"""Shared types and argument helpers for world adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ratchet.errors import NotFoundError
from ratchet.mocktools.world import CallContext, ToolArgumentError, WorldState

__all__ = [
    "Args",
    "Result",
    "Adapter",
    "req_str",
    "opt_str",
    "req_int",
    "str_list",
    "rows_arg",
    "not_found",
]

Args = Mapping[str, object]
Result = dict[str, object]
Adapter = Callable[[WorldState, Args, CallContext], Result]


def _bad(name: str, expected: str) -> ToolArgumentError:
    return ToolArgumentError(f"argument {name!r} must be {expected}", context={"arg": name})


def req_str(args: Args, name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _bad(name, "a non-empty string")
    return value


def opt_str(args: Args, name: str, default: str | None = None) -> str | None:
    value = args.get(name)
    if value is None:
        return default
    if not isinstance(value, str):
        raise _bad(name, "a string")
    return value if value.strip() else default


def req_int(args: Args, name: str) -> int:
    value = args.get(name)
    if isinstance(value, bool):
        raise _bad(name, "an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("#").isdigit():
        return int(value.strip().lstrip("#"))
    raise _bad(name, "an integer")


def str_list(args: Args, name: str, *, required: bool = False) -> list[str]:
    """A list of strings; a single string is accepted as a one-item list."""
    value = args.get(name)
    if value is None:
        if required:
            raise _bad(name, "a list of strings")
        return []
    if isinstance(value, str):
        return [value]
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise _bad(name, "a list of strings")
    return [str(v) for v in value]


def _cell(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def rows_arg(args: Args, name: str = "rows") -> list[list[str]]:
    """Rows as lists of strings; a flat list is one row; scalars are stringified."""
    value = args.get(name)
    if not isinstance(value, list) or not value:
        raise _bad(name, "a non-empty list of rows")
    if all(not isinstance(v, list) for v in value):
        return [[_cell(v) for v in value]]
    rows: list[list[str]] = []
    for row in value:
        if not isinstance(row, list):
            raise _bad(name, "a list of rows (lists)")
        rows.append([_cell(v) for v in row])
    return rows


def not_found(kind: str, ref: str) -> NotFoundError:
    return NotFoundError(f"{kind} {ref} not found", code="not_found", context={"kind": kind})
