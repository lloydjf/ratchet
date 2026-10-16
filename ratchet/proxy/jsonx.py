"""Typed accessors over decoded JSON (``object``), so mypy --strict holds without ``Any``."""

from __future__ import annotations

__all__ = ["as_dict", "as_list", "as_int", "as_str", "deep_merge"]


def as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(k): v for k, v in value.items()}
    return {}


def as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def deep_merge(base: dict[str, object], extra: dict[str, object]) -> dict[str, object]:
    """``extra`` wins; nested dicts are merged rather than replaced."""
    out = dict(base)
    for key, value in extra.items():
        current = out.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            out[key] = deep_merge(as_dict(current), as_dict(value))
        else:
            out[key] = value
    return out
