"""Pure local tools executed against the workspace: ``fs.*`` and read-only ``shell.run``.

These run for real inside the sandbox ("pure local tools"); writes and deletes
are reconciled from the workspace diff at task end, reads are reported to the mock tool
server's ``/notify`` hook by the caller. Every path is resolved (following symlinks) and
must stay inside the workspace; ``shell.run`` accepts only a small allowlist of read-only
commands (``ls cat head tail wc grep``) with no shell syntax at all (no pipes, redirects,
substitutions or chaining), each with an explicit per-command flag allowlist, and runs them
in pure Python (``readonly_shell``) — no process is ever spawned. The sandbox is the real
boundary; this is defence in depth.
"""

from __future__ import annotations

import shlex
from pathlib import Path

from ratchet.replay.harness.readonly_shell import (
    SHELL_COMMANDS,
    ShellRefused,
    parse_shell_argv,
    run_shell,
)

__all__ = [
    "ToolRefused",
    "SHELL_ALLOWLIST",
    "LocalTools",
    "resolve_in_workspace",
    "check_shell_command",
]

SHELL_ALLOWLIST = frozenset(SHELL_COMMANDS)
_SHELL_META = frozenset("|&;<>`$\n\r\\")
_MAX_READ = 64 * 1024
_MAX_OUTPUT = 16 * 1024


class ToolRefused(Exception):
    """The tool call was refused; ``str(exc)`` is safe to show the model."""


def resolve_in_workspace(workspace: Path, rel: str) -> Path:
    if not isinstance(rel, str) or "\x00" in rel:
        raise ToolRefused("invalid path")
    if Path(rel).is_absolute():
        raise ToolRefused("absolute paths are not allowed; use a path relative to the workspace")
    root = workspace.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root):
        raise ToolRefused("path escapes the workspace")
    return target


def check_shell_command(cmd: str, workspace: Path) -> list[str]:
    """Validate ``cmd`` and return its argv, or raise :class:`ToolRefused`."""
    if not isinstance(cmd, str) or not cmd.strip():
        raise ToolRefused("empty command")
    if any(ch in _SHELL_META for ch in cmd):
        raise ToolRefused("shell syntax (pipes, redirects, substitution, chaining) is not allowed")
    try:
        argv = shlex.split(cmd)
    except ValueError as exc:
        raise ToolRefused("unparseable command") from exc
    try:
        parse_shell_argv(argv, workspace)
    except ShellRefused as exc:
        raise ToolRefused(str(exc)) from exc
    return argv


def _clip(text: str, limit: int = _MAX_OUTPUT) -> tuple[str, bool]:
    return (text, False) if len(text) <= limit else (text[:limit], True)


class LocalTools:
    """``fs.read``, ``fs.list``, ``fs.write``, ``fs.delete``, ``shell.run`` on one workspace."""

    READS = frozenset({"fs.read", "fs.list", "shell.run"})

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def call(self, tool: str, args: dict[str, object]) -> dict[str, object]:
        """Run ``tool``; refusals and errors come back as ``{"error": ...}``."""
        handlers = {
            "fs.read": self._read,
            "fs.list": self._list,
            "fs.write": self._write,
            "fs.delete": self._delete,
            "shell.run": self._shell,
        }
        handler = handlers.get(tool)
        if handler is None:
            return {"error": f"unknown local tool {tool}"}
        try:
            return handler(args)
        except ToolRefused as exc:
            return {"error": str(exc)}
        except (OSError, UnicodeDecodeError) as exc:
            return {"error": f"{tool} failed: {type(exc).__name__}"}

    def _read(self, args: dict[str, object]) -> dict[str, object]:
        path = resolve_in_workspace(self.workspace, str(args.get("path", "")))
        if not path.is_file():
            return {"error": "no such file"}
        raw = path.read_bytes()
        text = raw[:_MAX_READ].decode("utf-8", errors="replace")
        return {"path": str(args.get("path")), "content": text, "truncated": len(raw) > _MAX_READ}

    def _list(self, args: dict[str, object]) -> dict[str, object]:
        rel = str(args.get("path") or ".")
        path = resolve_in_workspace(self.workspace, rel)
        if not path.is_dir():
            return {"error": "no such directory"}
        entries = [
            {"name": child.name, "type": "dir" if child.is_dir() else "file"}
            for child in sorted(path.iterdir())
            if child.name not in {".git", ".ratchet"}
        ]
        return {"path": rel, "entries": entries}

    def _write(self, args: dict[str, object]) -> dict[str, object]:
        path = resolve_in_workspace(self.workspace, str(args.get("path", "")))
        content = args.get("content")
        if not isinstance(content, str):
            return {"error": "content must be a string"}
        if path.is_dir():
            return {"error": "path is a directory"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {"ok": True, "path": str(args.get("path")), "bytes": len(content.encode("utf-8"))}

    def _delete(self, args: dict[str, object]) -> dict[str, object]:
        path = resolve_in_workspace(self.workspace, str(args.get("path", "")))
        if path == self.workspace.resolve() or path.is_dir():
            return {"error": "only files can be deleted"}
        if not path.exists():
            return {"error": "no such file"}
        path.unlink()
        return {"ok": True, "path": str(args.get("path"))}

    def _shell(self, args: dict[str, object]) -> dict[str, object]:
        argv = check_shell_command(str(args.get("cmd", "")), self.workspace)
        try:
            code, out, err = run_shell(parse_shell_argv(argv, self.workspace), self.workspace)
        except ShellRefused as exc:
            raise ToolRefused(str(exc)) from exc
        stdout, cut_out = _clip(out)
        stderr, cut_err = _clip(err, 2048)
        return {
            "exit_code": code,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": cut_out or cut_err,
        }
