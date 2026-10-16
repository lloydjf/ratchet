"""Structured logging.

One JSON line per event on stdout (stderr for CLI commands other than ``serve``, so a
command's own output stays clean; see :func:`set_log_stream`) with ``ts``, ``level``,
``event`` (dotted, stable), ``component``, ``request_id`` and the ids in scope. Message
content, tool arguments and secrets are never logged — only hashes and lengths.
``ratchet_debug`` enables a local content log file that is never shipped
(``debug-content.log`` is git- and image-ignored).
"""

from __future__ import annotations

import json
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Literal, TextIO

from ratchet import clock

__all__ = [
    "Logger",
    "get_logger",
    "bind",
    "request_id_var",
    "debug_content",
    "set_log_stream",
    "log_stream_name",
]

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40}
_SCOPE_KEYS = ("run_id", "edit_id", "batch_id", "verdict_id", "session_hash")

request_id_var: ContextVar[str | None] = ContextVar("ratchet_request_id", default=None)
_scope: ContextVar[dict[str, str]] = ContextVar("ratchet_log_scope", default={})  # noqa: B039

_FORBIDDEN_FIELDS = frozenset(
    {"messages", "content", "args", "arguments", "api_key", "authorization"}
)


StreamName = Literal["stdout", "stderr"]
_stream_name: list[StreamName] = ["stdout"]  # process-wide; resolved at write time


def set_log_stream(name: StreamName) -> None:
    """Where loggers without an explicit stream write. The CLI picks ``stderr`` for every
    command but ``serve``; the stream object is looked up per line, so a swapped
    ``sys.stderr`` (tests, CliRunner) is honoured."""
    _stream_name[0] = name


def log_stream_name() -> StreamName:
    return _stream_name[0]


class Logger:
    def __init__(self, component: str, stream: TextIO | None = None) -> None:
        self.component = component
        self._stream = stream

    def _emit(self, level: str, event: str, fields: dict[str, object]) -> None:
        from ratchet.config.settings import get_settings

        threshold = _LEVELS.get(get_settings().ratchet_log_level.upper(), 20)
        if _LEVELS[level] < threshold:
            return
        leaked = _FORBIDDEN_FIELDS.intersection(fields)
        if leaked:
            raise ValueError(f"refusing to log content fields {sorted(leaked)}")
        record: dict[str, object] = {
            "ts": clock.iso(clock.now()),
            "level": level,
            "event": event,
            "component": self.component,
            "request_id": request_id_var.get(),
        }
        record.update(_scope.get())
        record.update(fields)
        default = sys.stderr if _stream_name[0] == "stderr" else sys.stdout
        stream = self._stream or default
        stream.write(json.dumps(record, default=str, separators=(",", ":")) + "\n")
        stream.flush()

    def debug(self, event: str, **fields: object) -> None:
        self._emit("DEBUG", event, fields)

    def info(self, event: str, **fields: object) -> None:
        self._emit("INFO", event, fields)

    def warning(self, event: str, **fields: object) -> None:
        self._emit("WARNING", event, fields)

    def error(self, event: str, **fields: object) -> None:
        self._emit("ERROR", event, fields)


def get_logger(component: str) -> Logger:
    return Logger(component)


class bind:  # noqa: N801 - used as a context manager, reads like a verb
    """Bind ids into every log line emitted in this context: ``with bind(edit_id=...)``."""

    def __init__(self, **ids: str | None) -> None:
        unknown = set(ids) - set(_SCOPE_KEYS)
        if unknown:
            raise ValueError(f"unknown scope keys: {sorted(unknown)}")
        self._ids = {k: v for k, v in ids.items() if v is not None}
        self._token: object | None = None

    def __enter__(self) -> bind:
        merged = dict(_scope.get())
        merged.update(self._ids)
        self._token = _scope.set(merged)
        return self

    def __exit__(self, *_: object) -> None:
        if self._token is not None:
            _scope.reset(self._token)  # type: ignore[arg-type]


def debug_content(label: str, content: str) -> None:
    """Write content to the local-only debug log when RATCHET_DEBUG is on."""
    from ratchet.config.settings import get_settings

    settings = get_settings()
    if not settings.ratchet_debug or settings.ratchet_public_mode:
        return
    path = Path("debug-content.log")
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"--- {clock.iso(clock.now())} {label}\n{content}\n")
