"""Synthetic results for replay-mode cassette misses (``synthetic.yaml``).

Only read-class and write_reversible misses get one; hard classes are blocked by the
engine before this module is consulted. ``time.now`` is computed from the replay's frozen
clock rather than templated, since it is a pure function of the run's time.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ratchet.mocktools.adapters.info import time_now
from ratchet.mocktools.world import CallContext, blank_state
from ratchet.models import SideEffectClass

__all__ = ["SyntheticTemplates", "load_templates", "parse_templates", "synthetic_result"]

_FILE = Path(__file__).with_name("synthetic.yaml")
_ARG = re.compile(r"^\{\{arg:([A-Za-z0-9_]+)\}\}$")
_READ_CLASSES: frozenset[str] = frozenset({"none", "read"})


@dataclass(frozen=True)
class SyntheticTemplates:
    default_read: Mapping[str, object]
    default_write: Mapping[str, object]
    tools: Mapping[str, Mapping[str, object]]


def _mapping(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def parse_templates(document: object) -> SyntheticTemplates:
    doc = _mapping(document)
    tools = _mapping(doc.get("tools"))
    return SyntheticTemplates(
        default_read=_mapping(doc.get("default_read")),
        default_write=_mapping(doc.get("default_write")) or {"status": "ok"},
        tools={name: _mapping(spec) for name, spec in tools.items()},
    )


@lru_cache(maxsize=1)
def load_templates() -> SyntheticTemplates:
    return parse_templates(yaml.safe_load(_FILE.read_text(encoding="utf-8")))


def _render(value: object, args: Mapping[str, object]) -> object:
    if isinstance(value, str):
        match = _ARG.match(value)
        return args.get(match.group(1)) if match else value
    if isinstance(value, Mapping):
        return {str(k): _render(v, args) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, args) for v in value]
    return value


def synthetic_result(
    tool: str,
    args_norm: Mapping[str, object],
    side_effect: SideEffectClass,
    ctx: CallContext,
    templates: SyntheticTemplates | None = None,
) -> dict[str, object]:
    """The stand-in result for a missed call of ``tool``."""
    if tool == "time.now":
        return time_now(blank_state(), args_norm, ctx)
    active = templates or load_templates()
    template = active.tools.get(tool)
    if template is None:
        template = active.default_read if side_effect in _READ_CLASSES else active.default_write
    rendered = _render(template, args_norm)
    return rendered if isinstance(rendered, dict) else {}
