"""The Docker build context never carries secrets or runtime state.

``.dockerignore`` must exclude ``.env``, ``var/`` and ``data/`` (and keys, the debug content
log, ``.git``), while still sending everything the two images COPY. The matcher follows
Docker's rules: patterns are relative to the context root, ``*`` stays within one path
segment, ``**`` spans segments, a match on a directory excludes everything below it, and the
last matching pattern wins (``!`` re-includes).
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = [ROOT / "worker" / "Dockerfile", ROOT / "deploy" / "control.Dockerfile"]


def _rules() -> list[tuple[bool, re.Pattern[str]]]:
    rules: list[tuple[bool, re.Pattern[str]]] = []
    for raw in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        pattern = line[1:] if negate else line
        rules.append((negate, _compile(pattern.strip().strip("/"))))
    return rules


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out)


def excluded(path: str) -> bool:
    """True when Docker would leave ``path`` out of the build context."""
    parts = path.split("/")
    prefixes = ["/".join(parts[: n + 1]) for n in range(len(parts))]
    state = False
    for negate, regex in _rules():
        if any(regex.fullmatch(prefix) for prefix in prefixes):
            state = not negate
    return state


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "var/data/ratchet.db",
        "var/skills/triage/SKILL.md",
        "data/traces/run_1/steps.jsonl",
        "data/cassettes/gmail.send/abc.json",
        "pii.key",
        "debug-content.log",
        ".git/config",
        ".venv/bin/python",
        "dashboard/node_modules/react/index.js",
    ],
)
def test_secrets_and_runtime_state_stay_out_of_the_build_context(path: str) -> None:
    assert excluded(path), f"{path} would be sent to the Docker daemon"


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "uv.lock",
        "README.md",
        "LICENSE",
        "ratchet/__init__.py",
        "ratchet/judge/prompts/screen.md",
        "worker/entrypoint.py",
        "scenarios/1/scenario.yaml",
        "tests/fixtures/tavily/d1.json",
        "dashboard/package.json",
        "dashboard/package-lock.json",
        "dashboard/src/main.tsx",
        "deploy/control-entrypoint.sh",
    ],
)
def test_everything_the_images_copy_is_in_the_context(path: str) -> None:
    assert not excluded(path), f"{path} is excluded but an image COPYs it"


def test_copy_sources_in_the_dockerfiles_are_not_excluded() -> None:
    for dockerfile in DOCKERFILES:
        for line in dockerfile.read_text(encoding="utf-8").splitlines():
            words = line.split()
            if not words or words[0] != "COPY" or any(w.startswith("--from") for w in words):
                continue
            sources = [w for w in words[1:-1] if not w.startswith("--")]
            for source in sources:
                assert source not in {".", "./"}, f"{dockerfile.name}: COPY of the whole context"
                assert not excluded(source.rstrip("/")), (dockerfile.name, source)


def test_matcher_follows_docker_semantics() -> None:
    _compile.cache_clear()
    assert _compile("*.key").fullmatch("pii.key")
    assert not _compile("*.key").fullmatch("var/pii.key")
    assert _compile("**/__pycache__").fullmatch("ratchet/x/__pycache__")
    assert _compile("**/__pycache__").fullmatch("__pycache__")
