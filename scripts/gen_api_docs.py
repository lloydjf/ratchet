"""Render the dashboard API reference from ``schemas/openapi.json``.

Writes three things:

- the endpoint tables and per-endpoint details into ``docs/user/api.md`` (between its
  GENERATED markers);
- the error code table into the same region, from the registry in ``docs/CONTRACTS.md``
  §8 joined with the user-facing copy in ``ratchet/copy_errors.py``;
- ``docs/user/reference/api-models.md``: every response and request model with its fields.

``schemas/openapi.json`` is itself generated (``ratchet api export-openapi``) and checked
for drift in CI, so this page follows the code. Run through ``scripts/gen_docs.py``, or:

    uv run python scripts/gen_api_docs.py [--check]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(Path(__file__).resolve().parent)]

from _docgen import banner, replace_generated, write_if_changed  # noqa: E402

OPENAPI = ROOT / "schemas" / "openapi.json"
CONTRACTS = ROOT / "docs" / "CONTRACTS.md"
PAGE = ROOT / "docs" / "user" / "api.md"
MODELS_PAGE = ROOT / "docs" / "user" / "reference" / "api-models.md"
SCRIPT = "scripts/gen_api_docs.py"
METHODS = ("get", "post", "put", "patch", "delete")
TAG_TITLES = {
    "meta": "Meta and settings",
    "simulate": "Simulate",
    "verdicts": "Verdicts and actions",
    "edits": "Edits",
    "traces": "Traces and blessing",
    "events": "Events",
    "costs": "Costs",
    "digest": "Digest",
    "demo": "Demo analytics",
    "ops": "Operations",
}


def _cell(text: object) -> str:
    out = str(text or "").replace("|", "\\|").replace("<", "&lt;").replace("\n", " ")
    return re.sub(r"``([^`]+)``", r"`\1`", out).strip() or "—"


def _ref(schema: dict[str, Any] | None) -> str | None:
    if not schema:
        return None
    if "$ref" in schema:
        return str(schema["$ref"]).rsplit("/", 1)[-1]
    if schema.get("type") == "array":
        inner = _ref(schema.get("items"))
        return f"{inner}[]" if inner else "array"
    return None


def _model_link(name: str | None) -> str:
    if not name:
        return "—"
    bare = name.removesuffix("[]")
    suffix = "[]" if name.endswith("[]") else ""
    return f"[`{bare}{suffix}`](reference/api-models.md#{bare.lower()})"


def _type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return f"`{_ref(schema)}`"
    if "anyOf" in schema:
        parts = [_type(s) for s in schema["anyOf"] if s.get("type") != "null"]
        nullable = any(s.get("type") == "null" for s in schema["anyOf"])
        text = " or ".join(p for p in parts if p) or "any"
        return f"{text} or null" if nullable else text
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" for v in schema["enum"])
    kind = schema.get("type")
    if kind == "array":
        return f"array of {_type(schema.get('items', {}))}"
    if kind == "object":
        extra = schema.get("additionalProperties")
        return f"map of {_type(extra)}" if isinstance(extra, dict) and extra else "object"
    fmt = f" ({schema['format']})" if "format" in schema else ""
    return f"{kind}{fmt}" if kind else "any"


def _response(op: dict[str, Any]) -> tuple[str, str]:
    for status in ("200", "201", "204"):
        if status not in op.get("responses", {}):
            continue
        content = op["responses"][status].get("content", {})
        if "text/event-stream" in content:
            return status, "SSE stream (`text/event-stream`)"
        if not content:
            return status, "no body"
        text_types = [t for t in content if t.startswith("text/")]
        if text_types and "application/json" not in content:
            return status, f"text (`{text_types[0].split(';')[0]}`)"
        schema = content.get("application/json", {}).get("schema")
        return status, _model_link(_ref(schema)) if _ref(schema) else _cell(_type(schema or {}))
    return "—", "—"


def _operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    ops = []
    for path, item in spec["paths"].items():
        for method in METHODS:
            if method in item:
                ops.append((method.upper(), path, item[method]))
    return ops


def _anchor(method: str, path: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{method} {path}".lower()).strip("-")
    return slug


def _details(method: str, path: str, op: dict[str, Any]) -> list[str]:
    out = [f"### {method} `{path}` {{#{_anchor(method, path)}}}", ""]
    if op.get("description"):
        out += [_cell(op["description"]), ""]
    params = op.get("parameters", [])
    if params:
        out += ["| Parameter | In | Type | Required | Default |", "| --- | --- | --- | --- | --- |"]
        for p in params:
            schema = p.get("schema", {})
            default = f"`{schema['default']}`" if "default" in schema else "—"
            limits = []
            for key, label in (("minimum", "min"), ("maximum", "max"), ("maxLength", "max len")):
                if key in schema:
                    limits.append(f"{label} {schema[key]}")
            for alt in schema.get("anyOf", []):
                for key, label in (("minimum", "min"), ("maxLength", "max len")):
                    if key in alt:
                        limits.append(f"{label} {alt[key]}")
            kind = _type(schema) + (f" ({', '.join(limits)})" if limits else "")
            req = "yes" if p.get("required") else "no"
            out.append(f"| `{p['name']}` | {p['in']} | {kind} | {req} | {default} |")
        out.append("")
    body = op.get("requestBody", {}).get("content", {}).get("application/json", {})
    if body:
        out += [f"Request body: {_model_link(_ref(body.get('schema')))}", ""]
    status, model = _response(op)
    errors = sorted(k for k in op.get("responses", {}) if k not in ("200", "201", "204"))
    tail = f"; errors: {', '.join(f'`{e}`' for e in errors)}" if errors else ""
    out += [f"Response `{status}`: {model}{tail}.", ""]
    return out


def render_endpoints(spec: dict[str, Any]) -> list[str]:
    ops = _operations(spec)
    lines = [
        "## Endpoints",
        "",
        f"{len(ops)} operations, from `schemas/openapi.json` (API version "
        f"`{spec['info']['version']}`).",
        "",
        "| Method | Path | Summary | Response |",
        "| --- | --- | --- | --- |",
    ]
    for method, path, op in ops:
        _, model = _response(op)
        lines.append(
            f"| `{method}` | [`{path}`](#{_anchor(method, path)}) | {_cell(op.get('summary'))} "
            f"| {model} |"
        )
    lines.append("")
    by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
    for method, path, op in ops:
        by_tag.setdefault((op.get("tags") or ["other"])[0], []).append((method, path, op))
    for tag in [t for t in TAG_TITLES if t in by_tag] + [t for t in by_tag if t not in TAG_TITLES]:
        lines += [f"## {TAG_TITLES.get(tag, tag.title())}", ""]
        for method, path, op in by_tag[tag]:
            lines += _details(method, path, op)
    return lines


def _registry_rows() -> list[list[str]]:
    """The "Errors" table of docs/CONTRACTS.md §8, as cells."""
    text = CONTRACTS.read_text(encoding="utf-8")
    start = text.find("### Errors")
    if start < 0:
        raise SystemExit("docs/CONTRACTS.md has no '### Errors' registry")
    rows = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("### "):
            break
        if not line.startswith("|") or line.startswith("| ---") or line.startswith("| Code"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        if len(cells) >= 4:
            rows.append(cells[:4])
    return rows


def render_errors() -> list[str]:
    from ratchet.copy_errors import ERROR_COPY, REASON_TEXT

    lines = [
        "## Error codes",
        "",
        "Every stable code, from the registry in `docs/CONTRACTS.md` §8. The user-facing text",
        "is what the dashboard and CLI show: **long** from `ERROR_COPY` (dialogs, banners,",
        "toasts), **short** from `REASON_TEXT` (pair evidence, badges). A dash means the code",
        "is internal or operator-only.",
        "",
        "| Code | HTTP | Raised by | User-facing text |",
        "| --- | --- | --- | --- |",
    ]
    for code_cell, http, raised, _copy in _registry_rows():
        codes = re.findall(r"`([a-z_0-9]+)`", code_cell)
        texts = []
        for code in codes:
            long = ERROR_COPY.get(code)
            short = REASON_TEXT.get(code)
            if long:
                texts.append(f"**long:** {long.replace('{n} of {m} ', '')}")
            if short:
                texts.append(f"**short:** {short}")
        text = "<br>".join(_cell(t) for t in dict.fromkeys(texts)) or "—"
        lines.append(f"| {code_cell} | {http} | {raised} | {text} |")
    lines.append("")
    return lines


def render_models(spec: dict[str, Any]) -> str:
    schemas = spec["components"]["schemas"]
    lines = [
        "# API models",
        "",
        banner(SCRIPT, "schemas/openapi.json"),
        "Every model the [dashboard API](../api.md) reads or returns, with its fields. The",
        "Python source of truth is `ratchet/models.py` (core models) and",
        "`ratchet/dashboard_api/views.py` (view models).",
        "",
    ]
    for name in sorted(schemas):
        schema = schemas[name]
        lines += [f"## {name} {{#{name.lower()}}}", ""]
        if schema.get("description"):
            lines += [_cell(schema["description"]), ""]
        props = schema.get("properties", {})
        if not props:
            lines += [f"Type: {_type(schema)}.", ""]
            continue
        required = set(schema.get("required", []))
        lines += ["| Field | Type | Required | Description |", "| --- | --- | --- | --- |"]
        for field, sub in props.items():
            kind = _type(sub)
            kind = re.sub(
                r"`([A-Z][A-Za-z_]+)`",
                lambda m: f"[`{m[1]}`](#{m[1].lower()})" if m[1] in schemas else m[0],
                kind,
            )
            desc = _cell(sub.get("description", ""))
            req = "yes" if field in required else "no"
            lines.append(f"| `{field}` | {kind} | {req} | {desc} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail if a page is stale.")
    args = parser.parse_args(argv)
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    body = "\n".join(render_endpoints(spec) + render_errors())
    code = replace_generated(PAGE, body, script=SCRIPT, check=args.check)
    return max(code, write_if_changed(MODELS_PAGE, render_models(spec), check=args.check))


if __name__ == "__main__":
    raise SystemExit(main())
