"""Hermes ``--format stream-json`` parsing and version discovery.

Confirmed event shapes (v0.22.2): ``system.{subtype:"init",model,session_id}``,
``text.{text}`` (streamed in pieces), ``tool_use.{name,input}``,
``tool_result.{name,output,duration_ms,is_error}`` and
``result.{text,exit_code,error?,session_id,tokens,duration_ms}``. Lines that are not JSON can
appear on stdout (``Warning: Unknown toolsets: …``) and are counted, not parsed.

No event carries the Hermes version, so ``harness_version`` comes from the version baked
into the worker image (``RATCHET_HERMES_VERSION``) or, failing that, from one
``hermes --version`` call per process and binary (:func:`hermes_version`); that call takes
about 5 s because Hermes checks for updates, so it is never made per run.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["HermesRun", "parse_stream", "parse_version", "hermes_version"]

_VERSION = re.compile(r"Hermes Agent v(\d+\.\d+\.\d+)")
_VERSION_TIMEOUT_S = 30.0
_versions: dict[str, str] = {}
_versions_lock = threading.Lock()


@dataclass(frozen=True)
class HermesRun:
    """What one ``--format stream-json`` run reported."""

    final_text: str
    exit_code: int | None
    tool_uses: list[str] = field(default_factory=list)
    skills_viewed: list[str] = field(default_factory=list)
    session_id: str | None = None
    version: str | None = None
    error: str | None = None
    bad_lines: int = 0


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _first_str(obj: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def parse_stream(stdout: str) -> HermesRun:
    """Parse Hermes ``--format stream-json`` output (one JSON event per line)."""
    texts: list[str] = []
    tools: list[str] = []
    skills: list[str] = []
    final: str | None = None
    exit_code: int | None = None
    session = version = error = None
    bad = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = _as_dict(json.loads(line))
        except ValueError:
            bad += 1
            continue
        kind = event.get("type")
        if kind == "system":
            session = _first_str(event, "session_id", "sessionId") or session
            version = _first_str(event, "version", "hermes_version") or version
        elif kind == "text":
            texts.append(_first_str(event, "text", "content") or "")
        elif kind == "tool_use":
            name = _first_str(event, "name", "tool") or ""
            tools.append(name)
            args = _as_dict(event.get("input") or event.get("args"))
            skill = _first_str(args, "name", "skill")
            if name == "skill_view" and skill and skill not in skills:
                skills.append(skill)
        elif kind == "result":
            final = _first_str(event, "result", "text", "final") or final
            code = event.get("exit_code")
            exit_code = code if isinstance(code, int) else exit_code
            raw_error = event.get("error")
            error = str(raw_error) if raw_error else None
            session = _first_str(event, "session_id", "sessionId") or session
    final_text = final if final is not None else "".join(texts).strip()
    return HermesRun(final_text, exit_code, tools, skills, session, version, error, bad)


def parse_version(text: str) -> str | None:
    """``Hermes Agent v0.22.2 (2026.9.24) · stable …`` → ``0.22.2``."""
    match = _VERSION.search(text)
    return match.group(1) if match else None


def _probe(binary: str) -> str:
    with tempfile.TemporaryDirectory(prefix="ratchet-hermes-version-") as home:
        env = {"HERMES_HOME": home, "HOME": home, "PATH": "/usr/local/bin:/usr/bin:/bin"}
        try:
            proc = subprocess.run(
                [binary, "--version"],
                env=env,
                cwd=Path(home),
                capture_output=True,
                text=True,
                timeout=_VERSION_TIMEOUT_S,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "unknown"
    return parse_version(proc.stdout) or "unknown"


def hermes_version(binary: str, baked: str | None = None) -> str:
    """The Hermes version: ``baked`` (set at install time) wins; otherwise ``binary
    --version``, run at most once per process for each binary."""
    if baked:
        return baked.strip().removeprefix("v")
    with _versions_lock:
        if binary not in _versions:
            _versions[binary] = _probe(binary)
        return _versions[binary]
