"""Regenerate every generated part of the user documentation site (``docs/user/``).

1. ``scripts/gen_cli_docs.py``      → the command reference in ``docs/user/cli.md``
2. ``scripts/gen_settings_docs.py`` → the settings tables in ``docs/user/configuration.md``
3. ``scripts/gen_api_docs.py``      → the endpoints and error codes in ``docs/user/api.md``
   and ``docs/user/reference/api-models.md``
4. Reference copies: ``docs/adr/*.md``, ``docs/VERIFIED.md``, ``docs/CONTRACTS.md`` and
   ``FEEDBACK.md`` → ``docs/user/reference/`` (marked generated; the originals stay the
   source of truth), plus an ADR index
5. Screenshots: ``docs/img/*.png`` → ``docs/user/img/``

Then build the site:

    uv run python scripts/gen_docs.py && uv run --group docs mkdocs build --strict

``--check`` writes nothing and exits 1 when any generated file is stale (for CI).
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(HERE)]

# The generators import ratchet modules only; pin the offline modes anyway so nothing
# here could ever reach a live model or Tavily.
os.environ.setdefault("RATCHET_UPSTREAM", "sim")
os.environ.setdefault("RATCHET_TAVILY_MODE", "fixtures")

import gen_api_docs  # noqa: E402
import gen_cli_docs  # noqa: E402
import gen_settings_docs  # noqa: E402
from _docgen import banner, write_if_changed  # noqa: E402

SCRIPT = "scripts/gen_docs.py"
USER = ROOT / "docs" / "user"
REFERENCE = USER / "reference"
REPO_BLOB = "https://github.com/lloydjf/ratchet/blob/main/"

# source (relative to the repo root) → destination (relative to docs/user/reference/)
COPIES: dict[str, str] = {
    "docs/VERIFIED.md": "verified.md",
    "docs/CONTRACTS.md": "contracts.md",
    "FEEDBACK.md": "feedback.md",
}
_LINK = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)")
_ADR_STATUS = re.compile(r"^Status:\s*(?P<v>\S+)", re.MULTILINE)
_ADR_DATE = re.compile(r"^Date:\s*(?P<v>\S+)", re.MULTILINE)


def _all_copies() -> dict[str, str]:
    copies = dict(COPIES)
    for adr in sorted((ROOT / "docs" / "adr").glob("[0-9]*.md")):
        copies[f"docs/adr/{adr.name}"] = f"adr/{adr.name}"
    return copies


def _rewrite_links(text: str, source: str, copies: dict[str, str], dest: str) -> str:
    """Point relative links at the copied page when there is one, else at GitHub."""
    by_source = {Path(s).as_posix(): d for s, d in copies.items()}
    dest_dir = Path(dest).parent

    def fix(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if re.match(r"^[a-z]+:|^#|^/", target):
            return match.group(0)
        path, _, anchor = target.partition("#")
        resolved = os.path.normpath((Path(source).parent / path).as_posix())
        frag = f"#{anchor}" if anchor else ""
        if resolved in by_source:
            rel = os.path.relpath(by_source[resolved], dest_dir.as_posix() or ".")
            return f"[{label}]({Path(rel).as_posix()}{frag})"
        return f"[{label}]({REPO_BLOB}{resolved}{frag})"

    return _LINK.sub(fix, text)


def _with_banner(text: str, source: str) -> str:
    lines = text.splitlines()
    note = banner(SCRIPT, source, title="Generated copy")
    for i, line in enumerate(lines):
        if line.startswith("# "):
            return "\n".join([*lines[: i + 1], "", note, *lines[i + 1 :]]).rstrip() + "\n"
    return note + "\n" + text


def copy_reference(check: bool) -> int:
    copies = _all_copies()
    code = 0
    for source, dest in copies.items():
        text = (ROOT / source).read_text(encoding="utf-8")
        text = _with_banner(_rewrite_links(text, source, copies, dest), source)
        code = max(code, write_if_changed(REFERENCE / dest, text, check=check))
    return max(code, write_if_changed(REFERENCE / "adr" / "index.md", _adr_index(), check=check))


def _adr_index() -> str:
    rows = []
    for adr in sorted((ROOT / "docs" / "adr").glob("[0-9]*.md")):
        lines = adr.read_text(encoding="utf-8").splitlines()
        title = lines[0].lstrip("# ").strip()
        number, _, name = title.partition(" — ")
        head = "\n".join(lines[1:5])
        status = m.group("v") if (m := _ADR_STATUS.search(head)) else "—"
        date = m.group("v") if (m := _ADR_DATE.search(head)) else "—"
        rows.append(f"| [{number}]({adr.name}) | {name or title} | {status} | {date} |")
    return "\n".join(
        [
            "# Architecture decision records",
            "",
            banner(SCRIPT, "docs/adr/"),
            "Each ADR records one decision: its context, the decision, the consequences, and",
            "what would make the team reverse it. The originals live in `docs/adr/`.",
            "",
            "| ADR | Decision | Status | Date |",
            "| --- | --- | --- | --- |",
            *rows,
            "",
        ]
    )


def copy_images(check: bool) -> int:
    code = 0
    for image in sorted((ROOT / "docs" / "img").glob("*.png")):
        target = USER / "img" / image.name
        data = image.read_bytes()
        if target.exists() and target.read_bytes() == data:
            continue
        if check:
            print(f"stale: {target}", file=sys.stderr)
            code = 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        print(f"wrote {target}")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail if anything is stale.")
    args = parser.parse_args(argv)
    flag = ["--check"] if args.check else []
    codes = [
        gen_cli_docs.main(flag),
        gen_settings_docs.main(flag),
        gen_api_docs.main(flag),
        copy_reference(args.check),
        copy_images(args.check),
    ]
    return max(codes)


if __name__ == "__main__":
    raise SystemExit(main())
