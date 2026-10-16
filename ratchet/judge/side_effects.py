"""Side-effect classification of tool calls, applied at bless time.

Rules in ``side_effects.yaml`` first (glob over the dotted tool name, first match wins);
``shell.run`` is classified from its command: every command in the chain on the read-only
allowlist → ``read``; a destructive pattern anywhere (``rm``, ``find -delete``/``-exec``, …)
or an output redirection to a file → ``destructive``; otherwise
``write_reversible`` (it ran inside the sandbox and the workspace diff captures it). Only
tools no rule matches go to Nano (``classify_side_effect.md``); the answer is cached per tool
name in ``Store.side_effect_cache``. A Nano failure yields ``write_irreversible`` — an unknown
tool is treated as consequential, never as harmless (CONTRACTS §0), and is not cached.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass
from fnmatch import fnmatchcase
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import yaml

from ratchet.judge.prompts import get_spec
from ratchet.judge.schemas import SideEffectOut
from ratchet.judge.structured import ChatClient, call_structured
from ratchet.models import SideEffectClass
from ratchet.util import canonical_json, preview

__all__ = [
    "RULES_PATH",
    "Rules",
    "SideEffectCache",
    "load_rules",
    "classify_rule",
    "classify_shell",
    "classify",
    "UNKNOWN_FALLBACK",
]

RULES_PATH = Path(__file__).parent / "side_effects.yaml"
UNKNOWN_FALLBACK: SideEffectClass = "write_irreversible"
_CLASSES: frozenset[str] = frozenset(
    {
        "none",
        "read",
        "write_reversible",
        "write_irreversible",
        "destructive",
        "external_send",
        "spend",
    }
)
_CHAIN = re.compile(r"\|\||&&|[|;&\n]")


class SideEffectCache(Protocol):
    def side_effect_cached(self, tool: str) -> tuple[str, str] | None: ...

    def cache_side_effect(self, tool: str, side_effect: str, reason: str) -> None: ...


@dataclass(frozen=True)
class Rules:
    patterns: tuple[tuple[str, SideEffectClass], ...]
    shell_tool: str
    shell_read_only: frozenset[str]
    shell_destructive: tuple[re.Pattern[str], ...]
    shell_otherwise: SideEffectClass
    shell_redirect: SideEffectClass = "destructive"


def _as_class(value: object) -> SideEffectClass:
    text = str(value)
    if text not in _CLASSES:
        raise ValueError(f"unknown side-effect class {text!r} in side_effects.yaml")
    return text  # type: ignore[return-value]


@lru_cache(maxsize=4)
def load_rules(path: Path = RULES_PATH) -> Rules:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    shell = raw["shell"]
    return Rules(
        patterns=tuple((str(r["pattern"]), _as_class(r["class"])) for r in raw["rules"]),
        shell_tool=str(shell["tool"]),
        shell_read_only=frozenset(str(c) for c in shell["read_only"]),
        shell_destructive=tuple(re.compile(str(p)) for p in shell["destructive"]),
        shell_otherwise=_as_class(shell["otherwise"]),
        shell_redirect=_as_class(shell.get("redirect", "destructive")),
    )


def _command_text(args: dict[str, object]) -> str:
    for key in ("cmd", "command", "script"):
        value = args.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
    return ""


def _head(segment: str) -> str:
    try:
        words = shlex.split(segment)
    except ValueError:
        words = segment.split()
    words = [w for w in words if "=" not in w or w.startswith("-")]  # drop VAR=value prefixes
    if not words:
        return ""
    head = Path(words[0]).name
    if head in {"git", "pip", "npm"} and len(words) > 1:
        return f"{head}-{words[1]}"
    return head


def classify_shell(args: dict[str, object], rules: Rules | None = None) -> SideEffectClass:
    rules = rules or load_rules()
    command = _command_text(args).strip()
    if not command:
        return rules.shell_otherwise
    if any(p.search(command) for p in rules.shell_destructive):
        return "destructive"
    plain = re.sub(r"\d?>&\d|\d?>\s*/dev/null", " ", command)
    if ">" in plain:
        return rules.shell_redirect  # redirection writes (and may clobber) a file
    segments = [s for s in _CHAIN.split(plain) if s.strip()]
    if segments and all(_head(s) in rules.shell_read_only for s in segments):
        return "read"
    return rules.shell_otherwise


def classify_rule(
    tool: str, args: dict[str, object] | None = None, rules: Rules | None = None
) -> SideEffectClass | None:
    """The rule-table class for ``tool`` (``None`` when no rule matches)."""
    rules = rules or load_rules()
    if tool == rules.shell_tool:
        return classify_shell(args or {}, rules)
    for pattern, cls in rules.patterns:
        if fnmatchcase(tool, pattern):
            return cls
    return None


async def classify(
    tool: str,
    args_example: dict[str, object],
    client: ChatClient,
    store: SideEffectCache,
) -> tuple[SideEffectClass, str]:
    """(class, reason): rule table, then the per-tool cache, then Nano."""
    ruled = classify_rule(tool, args_example)
    if ruled is not None:
        return ruled, "rule"
    cached = await asyncio.to_thread(store.side_effect_cached, tool)
    if cached is not None and cached[0] in _CLASSES:
        return _as_class(cached[0]), cached[1]
    spec = get_spec("classify_side_effect")
    result = await call_structured(
        client,
        prompt=spec.name,
        tier=spec.tier,
        inputs={"tool": tool, "args_example": preview(canonical_json(args_example), 600)},
        schema=SideEffectOut,
        max_tokens=spec.max_tokens,
    )
    if result.value is None:
        return UNKNOWN_FALLBACK, result.failure_code or "schema_failure"
    value = result.value
    await asyncio.to_thread(store.cache_side_effect, tool, value.side_effect, value.reason)
    return value.side_effect, value.reason
