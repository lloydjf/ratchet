"""Cassette retention for ``ratchet purge --before <date>``.

After the old traces are deleted, a cassette under ``data/cassettes/<tool>/<key>.json`` is
deleted when it was recorded before the cut-off and no remaining trace references it: no
``trace.json`` step and no in-flight ``steps.tool.jsonl`` line (a proxy-captured run that
is not finalized yet) names ``(tool, cassette_key)``. Overlay and batch cassette sets live
elsewhere and are never touched. ``recorded_at`` comes from the cassette; a cassette that
cannot be parsed falls back to its file modification time.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from ratchet import clock

__all__ = ["referenced_cassettes", "purge_cassettes"]

_STEP_FILE = "steps.tool.jsonl"


def _tool_refs(steps: Iterable[object]) -> Iterator[tuple[str, str]]:
    for step in steps:
        if not isinstance(step, dict) or step.get("kind") != "tool":
            continue
        tool, key = step.get("tool"), step.get("cassette_key")
        if isinstance(tool, str) and isinstance(key, str) and key:
            yield tool, key


def _trace_steps(trace_dir: Path) -> list[object]:
    """Raw step dicts of one trace dir; raises ``OSError``/``ValueError`` if unreadable."""
    steps: list[object] = []
    trace = trace_dir / "trace.json"
    if trace.is_file():
        data = json.loads(trace.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("steps"), list):
            steps.extend(data["steps"])
    pending = trace_dir / _STEP_FILE
    if pending.is_file():
        for line in pending.read_text(encoding="utf-8").splitlines():
            if line.strip():
                steps.append(json.loads(line))
    return steps


def referenced_cassettes(traces_root: Path) -> set[tuple[str, str]]:
    """Every ``(tool, cassette_key)`` named by a trace (finalized or in flight)."""
    refs: set[tuple[str, str]] = set()
    if not traces_root.is_dir():
        return refs
    for trace_dir in sorted(p for p in traces_root.iterdir() if p.is_dir()):
        refs.update(_tool_refs(_trace_steps(trace_dir)))
    return refs


def _recorded_at(path: Path) -> datetime:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("recorded_at") if isinstance(data, dict) else None
        if isinstance(value, str):
            return clock.parse(value)
    except (OSError, ValueError):
        pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def purge_cassettes(cassettes_dir: Path, traces_root: Path, before: datetime) -> int:
    """Delete unreferenced cassettes recorded before ``before``; returns how many.

    If any trace cannot be read the purge keeps every cassette (returns 0): deleting a
    cassette a golden still needs would break its replays."""
    if not cassettes_dir.is_dir():
        return 0
    try:
        keep = referenced_cassettes(traces_root)
    except (OSError, ValueError):
        return 0
    removed = 0
    for tool_dir in sorted(p for p in cassettes_dir.iterdir() if p.is_dir()):
        for path in sorted(tool_dir.glob("*.json")):
            if (tool_dir.name, path.stem) in keep or _recorded_at(path) >= before:
                continue
            path.unlink(missing_ok=True)
            removed += 1
    return removed
