"""Parse a rendered judge prompt back into its sections (ADR 0018).

The labels are the literal text around each ``{{placeholder}}`` of the prompt template
(``ratchet/judge/prompts/<name>.md``), which CONTRACTS §4 pins verbatim. The
parser walks the template's literals in order and takes the text between consecutive
literals as the section value; the shared tail and the prompt-mode schema hint are cut from
the end first; nonce-tagged data blocks around untrusted inputs are unwrapped (the labels
sit outside them). Section values are data only: nothing inside them is ever interpreted as an
instruction, so "ignore previous instructions" inside an output changes no heuristic.
"""

from __future__ import annotations

import re
from functools import lru_cache

from ratchet.judge.delimiters import unwrap_at
from ratchet.judge.prompts import SHARED_TAIL, load_body

__all__ = ["SCHEMA_HINT", "prompt_name", "strip_tail", "template_parts", "parse_sections"]

SCHEMA_HINT = "\n\nThe JSON object must match this JSON schema:\n"
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def prompt_name(header: str | None) -> str | None:
    """``screen@0123456789ab`` → ``screen``."""
    if not header:
        return None
    name = header.split("@", 1)[0].strip()
    return name if name.isidentifier() else None


def strip_tail(text: str) -> str:
    cut = text.rfind(SCHEMA_HINT)
    if cut >= 0:
        text = text[:cut]
    text = text.rstrip()
    if text.endswith(SHARED_TAIL):
        text = text[: -len(SHARED_TAIL)].rstrip()
    return text


@lru_cache(maxsize=32)
def template_parts(name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(literals, placeholder names); ``len(literals) == len(names) + 1``."""
    body = load_body(name)
    pieces = _PLACEHOLDER.split(body)
    return tuple(pieces[0::2]), tuple(pieces[1::2])


def parse_sections(name: str, text: str) -> dict[str, str]:
    """Section values by placeholder name; a section that cannot be located is absent.

    A value wrapped in a nonce-tagged data block (:mod:`ratchet.judge.delimiters`) is
    unwrapped, and the search for the next label resumes after the block's END marker, so a
    label-like line inside the data can never start a new section.
    """
    try:
        literals, names = template_parts(name)
    except (OSError, ValueError):
        return {}
    body = strip_tail(text)
    start = body.find(literals[0]) if literals[0] else 0
    if start < 0:
        return {}
    pos = start + len(literals[0])
    values: dict[str, str] = {}
    for index, key in enumerate(names):
        after = literals[index + 1]
        block = unwrap_at(body, pos)
        if block is not None:
            values.setdefault(key, block[0].strip())
            pos = block[1]
            if not after.strip():
                break
            end = body.find(after, pos)
            if end < 0:
                break
            pos = end + len(after)
            continue
        if not after.strip():
            values.setdefault(key, body[pos:].strip())
            break
        end = body.find(after, pos)
        if end < 0:
            values.setdefault(key, body[pos:].strip())
            break
        values.setdefault(key, body[pos:end].strip())
        pos = end + len(after)
    return values
