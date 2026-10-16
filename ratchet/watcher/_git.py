"""Narrow, typed wrapper over GitPython's command runner.

Every git invocation in Ratchet goes through :func:`run_git`: a fixed environment (no
user or system config, so signing, hooks paths and aliases on the host cannot change
behavior; a fixed committer identity), a 10 s kill timeout and zero retries,
and every failure mapped to :class:`ratchet.errors.GitError` with a stable code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from git.cmd import Git
from git.exc import GitCommandNotFound

from ratchet.errors import GitError

__all__ = [
    "GIT_TIMEOUT_S",
    "COMMITTER",
    "GitResult",
    "git_env",
    "run_git",
    "run_git_bytes",
]

GIT_TIMEOUT_S = 10.0
COMMITTER = ("Ratchet", "ratchet@ratchet.local")


@dataclass(frozen=True)
class GitResult:
    status: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.status == 0


def git_env() -> dict[str, str]:
    """The explicit environment for git: isolated from host config, fixed identities."""
    name, email = COMMITTER
    return {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_EDITOR": "true",
        "GIT_MERGE_AUTOEDIT": "no",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "LC_ALL": "C",
    }


def _execute(
    cwd: Path, args: tuple[str, ...], timeout_s: float, *, binary: bool
) -> tuple[int, str | bytes, str]:
    runner = Git(str(cwd))
    try:
        status, out, err = runner.execute(
            ["git", *args],
            with_extended_output=True,
            with_exceptions=False,
            stdout_as_string=not binary,
            kill_after_timeout=timeout_s,
            env=git_env(),
            strip_newline_in_stdout=False,
        )
    except GitCommandNotFound as exc:
        raise GitError("git executable not found", code="git_unavailable") from exc
    except OSError as exc:
        raise GitError(f"git could not start: {type(exc).__name__}", code="git_error") from exc
    out_value: str | bytes = out if out is not None else (b"" if binary else "")
    return int(status), out_value, str(err or "")


def _check(args: tuple[str, ...], status: int, err: str) -> None:
    if status == 0:
        return
    verb = args[0] if args else "git"
    if "did not complete in" in err:
        raise GitError(f"git {verb} timed out", code="git_timeout", context={"verb": verb})
    detail = err.strip().splitlines()[-1][:300] if err.strip() else f"exit {status}"
    raise GitError(
        f"git {verb} failed: {detail}",
        code="git_error",
        context={"verb": verb, "status": status},
    )


def run_git(
    cwd: Path, *args: str, check: bool = True, timeout_s: float = GIT_TIMEOUT_S
) -> GitResult:
    """Run ``git <args>`` in ``cwd``; text output. ``check`` raises ``GitError`` on failure."""
    status, out, err = _execute(cwd, args, timeout_s, binary=False)
    if check:
        _check(args, status, err)
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else out
    return GitResult(status, text, err)


def run_git_bytes(cwd: Path, *args: str, timeout_s: float = GIT_TIMEOUT_S) -> bytes:
    """Run ``git <args>`` and return raw stdout (file contents, archives)."""
    status, out, err = _execute(cwd, args, timeout_s, binary=True)
    _check(args, status, err)
    return out if isinstance(out, bytes) else out.encode("utf-8")
