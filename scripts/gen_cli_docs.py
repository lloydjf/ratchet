"""Render the ``ratchet`` CLI reference into ``docs/user/cli.md``.

Imports the Typer app, walks every command and sub-command, and writes their usage, help
text, arguments and options between the GENERATED markers of the page. The prose around
the markers is hand-written and kept. Run through ``scripts/gen_docs.py``, or alone:

    uv run python scripts/gen_cli_docs.py [--check]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import typer
from typer import _click as click  # Typer ≥ 0.25 vendors click; its classes are not click's

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from _docgen import replace_generated  # noqa: E402

PAGE = ROOT / "docs" / "user" / "cli.md"
SCRIPT = "scripts/gen_cli_docs.py"
HIDDEN_OPTIONS = {"--help", "--install-completion", "--show-completion"}


def _command_tree() -> click.Command:
    from ratchet.cli.main import app

    return typer.main.get_command(app)


def _is_group(command: click.Command) -> bool:
    return hasattr(command, "list_commands")


def _default(param: click.Parameter) -> str:
    if getattr(param, "is_flag", False) and not getattr(param, "secondary_opts", None):
        return "off" if not param.default else "on"
    if param.required:
        return "required"
    value = param.default
    if value is None or value == () or callable(value):
        return "—"
    if value == "":
        return "empty"
    if isinstance(value, bool):
        names = list(getattr(param, "opts", [])) + list(getattr(param, "secondary_opts", []))
        flag = names[0] if value else (names[-1] if len(names) > 1 else "off")
        return f"`{flag}`"
    return f"`{value}`"


def _type(param: click.Parameter) -> str:
    if getattr(param, "is_flag", False):
        return "flag"
    kind = param.type
    choices = getattr(kind, "choices", None)
    if choices:
        return " \\| ".join(f"`{c}`" for c in choices)
    name = str(getattr(kind, "name", "text")).lower()
    repeatable = getattr(param, "multiple", False) and "repeatable" not in (
        getattr(param, "help", None) or ""
    )
    multiple = " (repeatable)" if repeatable else ""
    names = {"text": "text", "str": "text", "int": "integer", "integer": "integer"}
    return names.get(name, name) + multiple


def _cell(text: str | None) -> str:
    text = (text or "").replace("|", "\\|").replace("<", "&lt;").replace("\n", " ")
    return text.strip() or "—"


def _arg_name(param: click.Parameter) -> str:
    return (param.metavar or param.name or "").upper()


def _usage(ctx: click.Context, command: click.Command) -> str:
    """Typer's own spelling: ``ratchet bless [OPTIONS] [RUN_ID]``."""
    pieces = [ctx.command_path, "[OPTIONS]"]
    for p in command.params:
        if p.param_type_name == "argument":
            pieces.append(_arg_name(p) if p.required else f"[{_arg_name(p)}]")
    return " ".join(pieces)


def _render_command(command: Any, ctx: click.Context, depth: int) -> list[str]:
    out = [f"{'#' * min(depth + 2, 4)} `{ctx.command_path}`", ""] if depth else []
    if command.help:
        out += [_cell(command.help.split("\n\n")[0]).replace("\\|", "|"), ""]
    if _is_group(command):
        subs = command.list_commands(ctx)
        out += ["| Command | Does |", "| --- | --- |"]
        for name in subs:
            sub = command.get_command(ctx, name)
            if sub is None or sub.hidden:
                continue
            anchor = _anchor(f"{ctx.command_path} {name}")
            out.append(f"| [`{name}`](#{anchor}) | {_cell(sub.get_short_help_str(200))} |")
        out.append("")
        return out
    out += ["```text", _usage(ctx, command), "```", ""]
    args = [p for p in command.params if p.param_type_name == "argument"]
    opts = [
        p
        for p in command.params
        if p.param_type_name == "option" and not set(p.opts) & HIDDEN_OPTIONS
    ]
    if args:
        out += ["| Argument | Type | Required | Description |", "| --- | --- | --- | --- |"]
        for p in args:
            name = _arg_name(p)
            req = "yes" if p.required else "no"
            out.append(f"| `{name}` | {_type(p)} | {req} | {_cell(getattr(p, 'help', ''))} |")
        out.append("")
    if opts:
        out += ["| Option | Type | Default | Description |", "| --- | --- | --- | --- |"]
        for p in opts:
            names = ", ".join(f"`{o}`" for o in [*p.opts, *p.secondary_opts])
            out.append(f"| {names} | {_type(p)} | {_default(p)} | {_cell(p.help)} |")
        out.append("")
    return out


def _anchor(command_path: str) -> str:
    """MkDocs' toc slug for a heading like ``ratchet bench run`` (backticks are dropped)."""
    return command_path.strip().lower().replace(" ", "-")


def _walk(command: Any, ctx: click.Context, depth: int) -> list[str]:
    lines = _render_command(command, ctx, depth)
    if _is_group(command):
        for name in command.list_commands(ctx):
            sub = command.get_command(ctx, name)
            if sub is None or sub.hidden:
                continue
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            lines += _walk(sub, sub_ctx, depth + 1)
    return lines


def render() -> str:
    root = _command_tree()
    ctx = click.Context(root, info_name="ratchet")
    lines = _walk(root, ctx, 0)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail if the page is stale.")
    args = parser.parse_args(argv)
    return replace_generated(PAGE, render(), script=SCRIPT, check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
