"""Render every setting in ``ratchet/config/settings.py`` into ``docs/user/configuration.md``.

Fields come from ``Settings.model_fields`` (name, type, default, description), in source
order, grouped under the ``# --- Group ---`` comments of the class body. Every field maps
1:1 to an environment variable of the same name in upper case. Run through
``scripts/gen_docs.py``, or alone:

    uv run python scripts/gen_settings_docs.py [--check]
"""

from __future__ import annotations

import argparse
import re
import sys
import types
import typing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from _docgen import replace_generated  # noqa: E402

PAGE = ROOT / "docs" / "user" / "configuration.md"
SOURCE = ROOT / "ratchet" / "config" / "settings.py"
SCRIPT = "scripts/gen_settings_docs.py"

_GROUP = re.compile(r"^\s{4}# --- (?P<name>.+?) -{3,}\s*$")
_FIELD = re.compile(r"^\s{4}(?P<name>[a-z][a-z0-9_]*)\s*:")


def groups() -> list[tuple[str, list[str]]]:
    """``[(group title, [field names])]`` in source order."""
    out: list[tuple[str, list[str]]] = []
    in_class = False
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if line.startswith("class Settings("):
            in_class = True
            continue
        if in_class and line and not line.startswith(" "):
            break
        if not in_class:
            continue
        if match := _GROUP.match(line):
            out.append((match.group("name"), []))
        elif (match := _FIELD.match(line)) and out:
            out[-1][1].append(match.group("name"))
    return out


def _type(annotation: object) -> str:
    from pydantic import SecretStr

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal:
        return " \\| ".join(f"`{a}`" for a in args)
    if origin in (typing.Union, types.UnionType):
        inner = [a for a in args if a is not type(None)]
        text = " \\| ".join(_type(a) for a in inner)
        return f"{text} (optional)" if len(inner) < len(args) else text
    if origin is dict:
        return "JSON object"
    if annotation is SecretStr:
        return "secret"
    if annotation is Path:
        return "path"
    if isinstance(annotation, type):
        return {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}.get(
            annotation.__name__, annotation.__name__
        )
    return str(annotation)


def _default(field: object) -> str:
    from pydantic import SecretStr

    factory = getattr(field, "default_factory", None)
    value = factory() if factory is not None else getattr(field, "default", None)
    if value is None:
        return "unset"
    if isinstance(value, SecretStr):
        value = value.get_secret_value()
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, dict):
        return "`{}`" if not value else f"`{value}`"
    if value == "":
        return "empty"
    return f"`{value}`"


def _cell(text: str | None) -> str:
    text = (text or "").replace("|", "\\|").replace("<", "&lt;").replace("\n", " ")
    return text.strip() or "—"


def render() -> str:
    from ratchet.config.settings import Settings

    fields = Settings.model_fields
    seen: set[str] = set()
    lines: list[str] = [
        f"All {len(fields)} settings, in the order and groups of `ratchet/config/settings.py`.",
        "",
    ]
    for title, names in groups():
        lines += [f"### {title}", "", "| Env var | Type | Default | Description |"]
        lines.append("| --- | --- | --- | --- |")
        for name in names:
            field = fields[name]
            seen.add(name)
            env = name.upper()
            lines.append(
                f"| `{env}` | {_type(field.annotation)} | {_default(field)} "
                f"| {_cell(field.description)} |"
            )
        lines.append("")
    missing = sorted(set(fields) - seen)
    if missing:
        raise SystemExit(f"fields outside any group in settings.py: {', '.join(missing)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail if the page is stale.")
    args = parser.parse_args(argv)
    return replace_generated(PAGE, render(), script=SCRIPT, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
