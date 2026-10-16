"""Tool-argument normalization and cassette keys.

``normalize_args`` turns the arguments of one tool call into ``args_norm``: volatile
fields dropped (``volatile.yaml`` per tool plus the global key pattern), null values
dropped, order-free lists sorted, PII tokenized through an injected hook, keys sorted
recursively. ``cassette_key`` hashes the result; the same value is the ``ToolCall``'s
``args_hash``. Everything here is pure apart from reading the packaged YAML once.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ratchet.util import canonical_json, hash_json, sha256_hex

__all__ = [
    "Tokenizer",
    "DEFAULT_PATTERN",
    "ToolRules",
    "VolatileRules",
    "parse_rules",
    "load_rules",
    "normalize_args",
    "tokenize_deep",
    "cassette_key",
    "args_hash",
    "args_raw_hash",
    "is_volatile_key",
]

Tokenizer = Callable[[object], object]

DEFAULT_PATTERN = r"(^|_)(id|ts|timestamp|nonce|token|etag)$"
_VOLATILE_FILE = Path(__file__).with_name("volatile.yaml")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


@dataclass(frozen=True)
class ToolRules:
    drop: frozenset[str] = frozenset()
    keep: frozenset[str] = frozenset()
    sets: frozenset[str] = frozenset()


@dataclass(frozen=True)
class VolatileRules:
    pattern: re.Pattern[str]
    common: ToolRules
    tools: Mapping[str, ToolRules]

    def for_tool(self, tool: str) -> ToolRules:
        own = self.tools.get(tool, ToolRules())
        return ToolRules(
            drop=self.common.drop | own.drop,
            keep=self.common.keep | own.keep,
            sets=self.common.sets | own.sets,
        )


def _names(raw: object) -> frozenset[str]:
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(item) for item in raw)


def _tool_rules(raw: object) -> ToolRules:
    if not isinstance(raw, Mapping):
        return ToolRules()
    return ToolRules(
        drop=_names(raw.get("drop")), keep=_names(raw.get("keep")), sets=_names(raw.get("sets"))
    )


def parse_rules(document: object) -> VolatileRules:
    """Build rules from a parsed ``volatile.yaml`` document."""
    doc = document if isinstance(document, Mapping) else {}
    tools_raw = doc.get("tools")
    tools = tools_raw if isinstance(tools_raw, Mapping) else {}
    per_tool = {str(name): _tool_rules(spec) for name, spec in tools.items() if name != "*"}
    return VolatileRules(
        pattern=re.compile(str(doc.get("global_pattern", DEFAULT_PATTERN))),
        common=_tool_rules(tools.get("*")),
        tools=per_tool,
    )


@lru_cache(maxsize=1)
def load_rules() -> VolatileRules:
    """The packaged ``volatile.yaml``, parsed once per process."""
    return parse_rules(yaml.safe_load(_VOLATILE_FILE.read_text(encoding="utf-8")))


def _snake(key: str) -> str:
    return _CAMEL.sub("_", key).lower()


def is_volatile_key(key: str, rules: ToolRules, pattern: re.Pattern[str]) -> bool:
    if key in rules.drop:
        return True
    if key in rules.keep:
        return False
    return pattern.search(_snake(key)) is not None


def _clean(value: object, rules: ToolRules, pattern: re.Pattern[str]) -> object:
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key in sorted(value, key=str):
            item = value[key]
            name = str(key)
            if item is None or is_volatile_key(name, rules, pattern):
                continue
            cleaned = _clean(item, rules, pattern)
            if name in rules.sets and isinstance(cleaned, list):
                cleaned = sorted(cleaned, key=canonical_json)
            out[name] = cleaned
        return out
    if isinstance(value, list | tuple):
        return [_clean(item, rules, pattern) for item in value]
    return value


def _sort_keys(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _sort_keys(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list | tuple):
        return [_sort_keys(item) for item in value]
    return value


def tokenize_deep(tokenizer: Tokenizer, value: object) -> object:
    """Apply ``tokenizer`` to every value AND every dict key (a busy map keyed by email
    must not leak the address through its keys; ``tokenize_obj`` skips keys)."""
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in value.items():
            new_key = tokenizer(str(key))
            out[new_key if isinstance(new_key, str) else str(key)] = tokenize_deep(tokenizer, item)
        return out
    if isinstance(value, list | tuple):
        return [tokenize_deep(tokenizer, item) for item in value]
    return tokenizer(value)


def normalize_args(
    tool: str,
    args: Mapping[str, object],
    *,
    tokenizer: Tokenizer | None = None,
    rules: VolatileRules | None = None,
) -> dict[str, object]:
    """``args_norm`` for one call: volatile/null fields dropped, PII tokenized, keys sorted."""
    active = rules or load_rules()
    cleaned = _clean(args, active.for_tool(tool), active.pattern)
    if tokenizer is not None:
        cleaned = tokenize_deep(tokenizer, cleaned)
    result = _sort_keys(cleaned)
    if not isinstance(result, dict):
        raise TypeError("tokenizer must return a mapping for a mapping input")
    return result


def cassette_key(tool: str, args_norm: Mapping[str, object]) -> str:
    """``sha256(tool + "\\n" + canonical_json(args_norm))``."""
    return sha256_hex(tool + "\n" + canonical_json(dict(args_norm)))


def args_hash(tool: str, args_norm: Mapping[str, object]) -> str:
    """The ``ToolCall.args_hash``; identical to the cassette key by design."""
    return cassette_key(tool, args_norm)


def args_raw_hash(args: Mapping[str, object]) -> str:
    """Hash of the arguments as received (before normalization), kept on the cassette."""
    return hash_json(dict(args))
