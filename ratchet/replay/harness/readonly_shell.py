"""The read-only ``shell.run`` subset, implemented in pure Python (no subprocess, no shell).

Delegating to real coreutils let options such as
``sort --compress-program=sh``, ``sort -o /abs``, ``grep -f /abs``, ``diff --to-file=``,
``wc --files0-from=``, ``find -L`` or ``uniq in out`` execute code or touch files outside the
workspace. Nothing here spawns a process. Each command accepts only an explicit allowlist
of single-letter flags with no attached values (clusters such as ``-ln`` are fine); the only
valued option is ``-n <digits>`` for ``head``/``tail``. Every other non-flag argument is a
path that must resolve (following symlinks) inside the workspace — except ``grep``'s pattern
(a basic regular expression: ``. * [ ] ^ $`` only).
Long options (``--x``), ``-`` (stdin) and ``--`` are refused.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["SHELL_COMMANDS", "ShellRefused", "ShellCall", "parse_shell_argv", "run_shell"]


class ShellRefused(Exception):
    """The command was refused; ``str(exc)`` is safe to show the model."""


# command -> allowed single-letter flags
SHELL_COMMANDS: dict[str, frozenset[str]] = {
    "ls": frozenset("la1"),
    "cat": frozenset("n"),
    "head": frozenset(),
    "tail": frozenset(),
    "wc": frozenset("lwc"),
    "grep": frozenset("nivclF"),
}
_VALUED = {"head": "n", "tail": "n"}
_HIDDEN = frozenset({".git", ".ratchet"})
_MAX_FILE = 4 * 1024 * 1024
_MAX_PATTERN = 256


@dataclass
class ShellCall:
    program: str
    flags: frozenset[str]
    count: int | None = None
    pattern: str | None = None
    paths: list[str] = field(default_factory=list)


def _inside(workspace: Path, rel: str) -> Path:
    if not rel or "\x00" in rel or rel.startswith("~") or Path(rel).is_absolute():
        raise ShellRefused("paths outside the workspace are not allowed")
    root = workspace.resolve()
    target = (root / rel).resolve()
    if not target.is_relative_to(root) or ".." in Path(rel).parts:
        raise ShellRefused("path escapes the workspace")
    return target


def parse_shell_argv(argv: list[str], workspace: Path) -> ShellCall:
    """Validate ``argv`` against the per-command allowlist, or raise :class:`ShellRefused`."""
    program, rest = argv[0], argv[1:]
    allowed = SHELL_COMMANDS.get(program)
    if allowed is None:
        raise ShellRefused(f"command not allowed; allowed: {', '.join(sorted(SHELL_COMMANDS))}")
    flags: set[str] = set()
    call = ShellCall(program=program, flags=frozenset())
    i = 0
    while i < len(rest):
        arg = rest[i]
        i += 1
        if arg.startswith("-"):
            if arg.startswith("--") or arg == "-":
                raise ShellRefused(f"{program} {arg.split('=', 1)[0]} is not allowed")
            letters = arg[1:]
            if program in _VALUED and letters == _VALUED[program]:
                if i >= len(rest) or not rest[i].isdigit():
                    raise ShellRefused(f"{program} -n needs a line count")
                call.count = int(rest[i])
                i += 1
                continue
            bad = [ch for ch in letters if ch not in allowed]
            if bad:
                raise ShellRefused(f"{program} -{bad[0]} is not allowed")
            flags.update(letters)
            continue
        if program == "grep" and call.pattern is None:
            if len(arg) > _MAX_PATTERN:
                raise ShellRefused("grep pattern too long")
            call.pattern = arg
            continue
        _inside(workspace, arg)
        call.paths.append(arg)
    call.flags = frozenset(flags)
    if program == "grep" and call.pattern is None:
        raise ShellRefused("grep needs a pattern")
    if program not in {"ls"} and not call.paths:
        raise ShellRefused(f"{program} needs a file path (stdin is not available)")
    return call


@dataclass
class _Out:
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    code: int = 0

    def fail(self, program: str, rel: str, why: str) -> None:
        self.stderr.append(f"{program}: {rel}: {why}")
        self.code = max(self.code, 2 if program == "grep" else 1)


def _read(workspace: Path, rel: str, out: _Out, program: str) -> str | None:
    path = _inside(workspace, rel)
    if path.is_dir():
        out.fail(program, rel, "Is a directory")
        return None
    if not path.is_file():
        out.fail(program, rel, "No such file or directory")
        return None
    with path.open("rb") as fh:
        return fh.read(_MAX_FILE).decode("utf-8", errors="replace")


def _ls(call: ShellCall, workspace: Path, out: _Out) -> None:
    targets = call.paths or ["."]
    for rel in targets:
        path = _inside(workspace, rel)
        if not path.exists():
            out.fail("ls", rel, "No such file or directory")
            continue
        children = sorted(path.iterdir()) if path.is_dir() else [path]
        if len(targets) > 1 and path.is_dir():
            out.stdout.append(f"{rel}:")
        for child in children:
            if child.name in _HIDDEN or (child.name.startswith(".") and "a" not in call.flags):
                continue
            if "l" in call.flags:
                kind = "d" if child.is_dir() else "-"
                size = 0 if child.is_dir() else child.stat().st_size
                out.stdout.append(f"{kind} {size:>9} {child.name}")
            else:
                out.stdout.append(child.name)


def _lines(text: str) -> list[str]:
    return text.splitlines()


def _cat(call: ShellCall, workspace: Path, out: _Out) -> None:
    n = 0
    for rel in call.paths:
        text = _read(workspace, rel, out, "cat")
        if text is None:
            continue
        for line in _lines(text):
            n += 1
            out.stdout.append(f"{n:>6}\t{line}" if "n" in call.flags else line)


def _head_tail(call: ShellCall, workspace: Path, out: _Out) -> None:
    count = 10 if call.count is None else call.count
    for idx, rel in enumerate(call.paths):
        text = _read(workspace, rel, out, call.program)
        if text is None:
            continue
        if len(call.paths) > 1:
            out.stdout.append(("" if idx == 0 else "\n") + f"==> {rel} <==")
        lines = _lines(text)
        picked = lines[:count] if call.program == "head" else lines[-count:] if count else []
        out.stdout.extend(picked)


def _wc(call: ShellCall, workspace: Path, out: _Out) -> None:
    keys = [k for k in "lwc" if k in call.flags] or ["l", "w", "c"]
    totals = dict.fromkeys(keys, 0)
    for rel in call.paths:
        text = _read(workspace, rel, out, "wc")
        if text is None:
            continue
        counts = {"l": text.count("\n"), "w": len(text.split()), "c": len(text.encode())}
        for k in keys:
            totals[k] += counts[k]
        out.stdout.append(" ".join(str(counts[k]) for k in keys) + f" {rel}")
    if len(call.paths) > 1:
        out.stdout.append(" ".join(str(totals[k]) for k in keys) + " total")


def _matcher(call: ShellCall) -> Callable[[str], bool]:
    pattern = call.pattern or ""
    icase = "i" in call.flags
    if "F" in call.flags:
        needle = pattern.lower() if icase else pattern
        return lambda line: needle in (line.lower() if icase else line)
    # Basic regular expressions: only ``. * [ ] ^ $`` are special (backslashes never reach
    # here), so there are no groups or alternation and no catastrophic backtracking.
    translated = "".join("\\" + ch if ch in "+?|(){}" else ch for ch in pattern)
    try:
        rx = re.compile(translated, re.IGNORECASE if icase else 0)
    except re.error as exc:
        raise ShellRefused("invalid grep pattern") from exc
    return lambda line: rx.search(line) is not None


def _grep(call: ShellCall, workspace: Path, out: _Out) -> None:
    match = _matcher(call)
    invert, many = "v" in call.flags, len(call.paths) > 1
    found = False
    for rel in call.paths:
        text = _read(workspace, rel, out, "grep")
        if text is None:
            continue
        hits = [(no, line) for no, line in enumerate(_lines(text), 1) if match(line) != invert]
        found = found or bool(hits)
        prefix = f"{rel}:" if many else ""
        if "l" in call.flags:
            out.stdout.extend([rel] if hits else [])
        elif "c" in call.flags:
            out.stdout.append(f"{prefix}{len(hits)}")
        else:
            for no, line in hits:
                out.stdout.append(f"{prefix}{no}:{line}" if "n" in call.flags else prefix + line)
    if out.code == 0 and not found:
        out.code = 1


_RUNNERS: dict[str, Callable[[ShellCall, Path, _Out], None]] = {
    "ls": _ls,
    "cat": _cat,
    "head": _head_tail,
    "tail": _head_tail,
    "wc": _wc,
    "grep": _grep,
}


def run_shell(call: ShellCall, workspace: Path) -> tuple[int, str, str]:
    """(exit_code, stdout, stderr) for a validated call; never spawns a process."""
    out = _Out()
    _RUNNERS[call.program](call, workspace, out)
    stdout = "\n".join(out.stdout) + ("\n" if out.stdout else "")
    stderr = "\n".join(out.stderr) + ("\n" if out.stderr else "")
    return out.code, stdout, stderr
