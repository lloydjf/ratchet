"""Small, pure text helpers shared by the simulator's agent policies and judge heuristics.

Simulator output is test infrastructure (ADR 0018), never model output.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "NUMBER",
    "KV_LINE",
    "normalize_ws",
    "content_lines",
    "mask_numbers",
    "kv",
    "words",
    "jaccard",
    "numbers",
    "decimals",
    "money",
    "fixed",
    "first_sentence",
    "bullet_items",
    "backticked",
]

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
# "Key: value" lines: a short label that starts with a letter, a colon, then a value.
KV_LINE = re.compile(r"^\s*(?:[-*]\s+)?([A-Za-z][A-Za-z0-9 _/#().'&-]{0,48}?):\s+(\S.*?)\s*$")
_WORD = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
_SENTENCE = re.compile(r"^(.+?[.!?])(?=\s|$)", re.DOTALL)
_BACKTICK = re.compile(r"`([^`]+)`")


def normalize_ws(text: str) -> str:
    """Collapse runs of spaces and tabs, strip each line, drop blank lines."""
    return "\n".join(content_lines(text))


def content_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def mask_numbers(text: str) -> str:
    """Replace every number with ``#`` so lines that differ only in figures compare equal."""
    return NUMBER.sub("#", text)


def kv(line: str) -> tuple[str, str] | None:
    match = KV_LINE.match(line)
    if match is None:
        return None
    key = match.group(1).strip()
    return (key, match.group(2)) if key else None


def words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def jaccard(a: str, b: str) -> float:
    left, right = set(words(a)), set(words(b))
    if not left and not right:
        return 1.0
    return len(left & right) / len(left | right)


def numbers(text: str) -> list[str]:
    return NUMBER.findall(text)


def decimals(text: str) -> list[str]:
    """Numbers that carry a decimal point (``1.0800``), ignoring dates and integers."""
    clean = re.sub(r"\d{4}-\d{2}-\d{2}(?:T[\d:.]+Z?)?", " ", text)
    return re.findall(r"(?<![\d.])\d+\.\d+(?![\d.])", clean)


def fixed(value: float | Decimal, places: int) -> str:
    """Round half up (receipts and FX tables do), never banker's rounding."""
    quantum = Decimal(1).scaleb(-places)
    return str(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def money(value: float | Decimal) -> str:
    return fixed(value, 2)


def first_sentence(text: str) -> str:
    clean = " ".join(text.split())
    match = _SENTENCE.match(clean)
    return match.group(1).strip() if match else clean


def bullet_items(text: str) -> list[str]:
    """``- item`` lines of a block (criteria lists, evidence lines)."""
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ")):
            items.append(stripped[2:].strip())
    return items


def backticked(text: str) -> list[str]:
    return _BACKTICK.findall(text)
