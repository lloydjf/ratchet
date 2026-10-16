#!/usr/bin/env python3
"""Secret (and optional PII) scanner for the git tree, traces, cassettes and logs.

Used by ``tests/security/test_secrets_leak.py`` and by the CI ``security`` job. Stdlib only
(``zstandard`` is used when importable, to look inside ``*.zst`` trace/workspace blobs).

What it flags
-------------
* **Configured values** — the exact value of every secret setting (``NEBIUS_TOKEN_FACTORY_KEY``,
  ``TAVILY_API_KEY``, ``NEBIUS_API_KEY``, the S3 keys, ``RATCHET_ACTION_TOKEN_SECRET``), taken
  from ``--value``, ``--env-file`` and/or ``--values-from-env``. A configured value is never
  allowlisted: it is a finding in every file, including tests.
* **Patterns** — ``sk-…``, ``tvly-…``, GitHub/AWS/Slack tokens, PEM private keys, long
  ``Authorization: Bearer`` values, and ``SECRET_VAR=<concrete value>`` assignments.
  Paths in the allowlist file (``scripts/secret_scan_allow.txt``) are exempt from pattern
  rules only, e.g. the fake PII corpus that exists to exercise the tokenizer.
* **PII** (``--pii``) — raw email addresses and phone numbers (over cassettes/traces).

Findings print as ``path:line: rule  <redacted>``; the matched secret itself is never echoed.
Exit codes: 0 clean, 1 findings, 2 usage error.

Examples::

    python scripts/secret_scan.py --git --untracked            # CI: the git tree
    python scripts/secret_scan.py --values-from-env --pii var/data
    python scripts/secret_scan.py --env-file .env var/data      # local: your real keys
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import gzip
import io
import json
import os
import re
import subprocess
import sys
import zlib
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

__all__ = [
    "SECRET_VARS",
    "Finding",
    "Scanner",
    "load_allowlist",
    "values_from_env",
    "values_from_env_file",
    "main",
]

SECRET_VARS: tuple[str, ...] = (
    "NEBIUS_TOKEN_FACTORY_KEY",
    "TAVILY_API_KEY",
    "NEBIUS_API_KEY",
    "NEBIUS_S3_ACCESS_KEY_ID",
    "NEBIUS_S3_SECRET_ACCESS_KEY",
    "RATCHET_ACTION_TOKEN_SECRET",
    "RATCHET_NOTIFY_WEBHOOK",
    "CLOUDFLARE_API_TOKEN",
)

# Values shorter than this, or containing a placeholder marker, are not treated as secrets.
MIN_VALUE_LEN = 8
_PLACEHOLDERS = (
    "change-me",
    "changeme",
    "example",
    "placeholder",
    "your-",
    "xxxx",
    "dummy",
    "fake",
)

_VAR_ALT = "|".join(SECRET_VARS)
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("tavily_key", re.compile(r"\btvly-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack_webhook", re.compile(r"hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}")),
    ("telegram_bot_token", re.compile(r"api\.telegram\.org/bot\d{6,}:[A-Za-z0-9_-]{30,}")),
    ("cloudflare_token", re.compile(r"\bcfat_[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{30,}\.eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}")),
    (
        "bearer_header",
        re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?bearer\s+[A-Za-z0-9._~+/-]{20,}"),
    ),
    (
        "secret_assignment",
        re.compile(
            rf"\b(?:{_VAR_ALT})[\"']?\s*[=:]\s*[\"']?(?P<value>[^\s\"'#$<{{}}()`,;]{{12,}})"
        ),
    ),
)

EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
PHONE = re.compile(
    r"(?<![\w+])(?:"
    r"\+\d{1,3}[\s.-]?(?:\(\d{1,4}\)[\s.-]?)?\d{1,4}(?:[\s.-]?\d{2,4}){2,4}"
    r"|\(\d{3}\)\s?\d{3}[\s.-]\d{4}"
    r"|\d{3}[.-]\d{3}[.-]\d{4}"
    r")(?![\w])"
)

_SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "__pycache__", ".mypy_cache"})
_SKIP_DIRS |= {".ruff_cache", ".pytest_cache"}
_MAX_BYTES = 64 * 1024 * 1024  # per file; traces and cassettes are far smaller
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\t]{6,}")
DEFAULT_ALLOWLIST = Path(__file__).resolve().with_name("secret_scan_allow.txt")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    preview: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}  {self.preview}"


def redact(value: str) -> str:
    """Never echo a secret: keep 4 leading characters and the length."""
    return f"{value[:4]}… ({len(value)} chars)"


# `settings.nebius_api_key`, `s.tavily_api_key`: code that names a secret, not a secret.
_CODE_REF = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")


def is_placeholder(value: str) -> bool:
    lowered = value.lower()
    if len(value) < MIN_VALUE_LEN or any(mark in lowered for mark in _PLACEHOLDERS):
        return True
    return bool(_CODE_REF.fullmatch(value))


def values_from_env(environ: dict[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return [env[name] for name in SECRET_VARS if env.get(name) and not is_placeholder(env[name])]


def values_from_env_file(path: Path) -> list[str]:
    values: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.removeprefix("export ").partition("=")
        value = value.strip().strip("'\"")
        if name.strip() in SECRET_VARS and value and not is_placeholder(value):
            values.append(value)
    return values


def load_allowlist(path: Path | None) -> list[str]:
    if path is None or not path.exists():
        return []
    globs: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        entry = raw.split("#", 1)[0].strip()
        if entry:
            globs.append(entry)
    return globs


def _read_text(path: Path) -> str | None:
    """Text of a file. Compressed blobs (``.zst``, ``.gz``) are opened; binary files
    (SQLite pages, WAL, tarballs) are reduced to their printable runs, like ``strings``."""
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_BYTES)
    except OSError:
        return None
    if path.suffix == ".zst":
        data = _unzstd(data) or data
    elif path.suffix == ".gz":
        with contextlib.suppress(OSError, EOFError, zlib.error):
            data = gzip.decompress(data)
    if b"\x00" in data[:8192]:
        return "\n".join(m.group(0).decode("ascii") for m in _PRINTABLE_RUN.finditer(data))
    return data.decode("utf-8", errors="replace")


def _unzstd(data: bytes) -> bytes | None:
    try:
        import zstandard
    except ImportError:
        return None
    try:
        with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
            return reader.read(_MAX_BYTES)
    except zstandard.ZstdError:
        return None


class Scanner:
    def __init__(
        self,
        values: Iterable[str] = (),
        *,
        pii: bool = False,
        allow_globs: Sequence[str] = (),
        root: Path | None = None,
    ) -> None:
        self.values = sorted({v for v in values if v and not is_placeholder(v)}, key=len)[::-1]
        self.pii = pii
        self.allow_globs = list(allow_globs)
        self.root = root

    def _rel(self, path: Path) -> str:
        if self.root is not None:
            try:
                return path.resolve().relative_to(self.root.resolve()).as_posix()
            except ValueError:
                pass
        return path.as_posix()

    def _allowed(self, rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, glob) for glob in self.allow_globs)

    def scan_text(self, rel: str, text: str) -> list[Finding]:
        findings: list[Finding] = []
        allowed = self._allowed(rel)
        for number, line in enumerate(text.splitlines(), start=1):
            for value in self.values:
                if value in line:
                    findings.append(Finding(rel, number, "configured_secret", redact(value)))
            if not allowed:
                findings += self._patterns(rel, number, line)
            if self.pii:
                for rule, regex in (("pii_email", EMAIL), ("pii_phone", PHONE)):
                    for match in regex.finditer(line):
                        findings.append(Finding(rel, number, rule, redact(match.group(0))))
        return findings

    @staticmethod
    def _patterns(rel: str, number: int, line: str) -> list[Finding]:
        found: list[Finding] = []
        for rule, regex in PATTERNS:
            for match in regex.finditer(line):
                value = match.groupdict().get("value") or match.group(0)
                if is_placeholder(value):
                    continue
                found.append(Finding(rel, number, rule, redact(value)))
        return found

    def scan_file(self, path: Path) -> list[Finding]:
        text = _read_text(path)
        return [] if text is None else self.scan_text(self._rel(path), text)

    def scan_paths(self, paths: Iterable[Path]) -> list[Finding]:
        findings: list[Finding] = []
        for path in _walk(paths):
            findings += self.scan_file(path)
        return findings


def _walk(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_file():
            yield path
        elif path.is_dir():
            for dirpath, dirnames, filenames in os.walk(path):
                dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
                for name in sorted(filenames):
                    yield Path(dirpath) / name


def git_files(root: Path, *, untracked: bool) -> list[Path]:
    """Tracked files, plus untracked-but-not-ignored ones (about to be committed)."""
    argv = ["git", "-C", str(root), "ls-files", "-z", "--cached"]
    if untracked:
        argv += ["--others", "--exclude-standard"]
    out = subprocess.run(argv, check=True, capture_output=True).stdout.decode()
    files = {root / name for name in out.split("\0") if name}
    return sorted(p for p in files if p.is_file())


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("paths", nargs="*", type=Path, help="files or directories to scan")
    parser.add_argument("--git", action="store_true", help="scan git-tracked files of --root")
    parser.add_argument("--untracked", action="store_true", help="with --git: add untracked")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repo root (default: .)")
    parser.add_argument("--value", action="append", default=[], help="a secret value to find")
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--values-from-env", action="store_true", help="read SECRET_VARS")
    parser.add_argument("--pii", action="store_true", help="also flag raw emails and phones")
    parser.add_argument("--allow", type=Path, default=DEFAULT_ALLOWLIST, help="allowlist file")
    parser.add_argument("--no-allow", action="store_true", help="ignore the allowlist")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)
    if not args.paths and not args.git:
        parser.error("give paths to scan and/or --git")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse(argv)
    values: list[str] = list(args.value)
    for env_file in args.env_file:
        values += values_from_env_file(env_file)
    if args.values_from_env:
        values += values_from_env()
    allow = [] if args.no_allow else load_allowlist(args.allow)
    scanner = Scanner(values, pii=args.pii, allow_globs=allow, root=args.root)
    targets: list[Path] = list(args.paths)
    if args.git:
        targets += git_files(args.root, untracked=args.untracked)
    findings = scanner.scan_paths(targets)
    if args.json:
        print(json.dumps([asdict(f) for f in findings], indent=2))
    else:
        for finding in findings:
            print(finding.render())
        noun = "finding" if len(findings) == 1 else "findings"
        print(f"secret_scan: {len(findings)} {noun}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
