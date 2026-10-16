"""Tolerant cassette matching, pure functions.

A replay call that misses the exact key may still be the *same* call with cosmetic
differences (a trailing space, different capitalization, one reworded word in a long
body). A candidate cassette matches tolerantly when it is for the same tool, has the same
set of (non-volatile) top-level keys, every non-string leaf is equal, and the Jaccard
similarity of the word tokens of all string leaves, after whitespace and case
normalization, is at least the threshold (0.9).

Tolerance never applies to consequential calls. Tools whose class is in
``HARD_SIDE_EFFECTS`` (send, spend, destructive, irreversible writes) match exactly or not at
all, so ``gmail.send`` to ``attacker@evil`` can never be answered from the cassette for the
recorded recipient. ``write_reversible`` calls match tolerantly only when every string leaf
under an identity-like key (recipient, channel, amount, account, any ``*_id`` …) is exactly
equal (:func:`tolerance_for`, ``Fingerprint.identity``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from ratchet.models import HARD_SIDE_EFFECTS

__all__ = [
    "TOLERANT_THRESHOLD",
    "IDENTITY_KEY",
    "Tolerance",
    "tolerance_for",
    "Fingerprint",
    "fingerprint",
    "jaccard",
    "tolerant_score",
    "best_tolerant",
]

TOLERANT_THRESHOLD = 0.9
_WORD = re.compile(r"\S+")
# Keys whose string values identify *who/what/how much* a write targets.
IDENTITY_KEY = re.compile(
    r"(^|_)(to|cc|bcc|from|recipients?|channel|amount|currency|account|iban|email|phone|"
    r"user(name)?|owner|repo|branch|path|number|issue|pr|id|ids|key|sku|price|quantity)$"
    r"|_id$",
    re.IGNORECASE,
)
Tolerance = Literal["exact", "identity", "full"]


def tolerance_for(side_effect: str | None) -> Tolerance:
    """How loosely a replay call of this class may match a cassette."""
    if side_effect is None or side_effect in HARD_SIDE_EFFECTS:
        return "exact"
    if side_effect == "write_reversible":
        return "identity"
    return "full"


@dataclass(frozen=True)
class Fingerprint:
    """What tolerant matching compares: key set, exact non-string leaves, string tokens."""

    keys: frozenset[str]
    scalars: frozenset[tuple[str, str]]
    tokens: frozenset[str]
    identity: frozenset[tuple[str, str]] = frozenset()


def _walk(
    value: object,
    path: str,
    scalars: set[tuple[str, str]],
    tokens: set[str],
    identity: set[tuple[str, str]],
    under_identity: bool = False,
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            marked = under_identity or IDENTITY_KEY.search(str(key)) is not None
            _walk(item, child, scalars, tokens, identity, marked)
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _walk(item, f"{path}[{index}]", scalars, tokens, identity, under_identity)
    elif isinstance(value, str):
        if under_identity:
            identity.add((path, value))
        # Tokens are path-scoped so a word moving between fields still counts as a change.
        field = re.sub(r"\[\d+\]", "[]", path)
        for word in _WORD.findall(value.casefold()):
            tokens.add(f"{field}:{word}")
        if not value.strip():
            tokens.add(f"{field}:")
    else:
        scalars.add((path, repr(value)))


def fingerprint(args_norm: Mapping[str, object]) -> Fingerprint:
    scalars: set[tuple[str, str]] = set()
    tokens: set[str] = set()
    identity: set[tuple[str, str]] = set()
    _walk(args_norm, "", scalars, tokens, identity)
    return Fingerprint(
        frozenset(str(k) for k in args_norm),
        frozenset(scalars),
        frozenset(tokens),
        frozenset(identity),
    )


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def tolerant_score(
    call: Fingerprint, candidate: Fingerprint, *, strict_identity: bool = False
) -> float | None:
    """Similarity in [0, 1], or None when the candidate is structurally a different call
    (or, with ``strict_identity``, targets a different recipient/channel/amount/id)."""
    if call.keys != candidate.keys or call.scalars != candidate.scalars:
        return None
    if strict_identity and call.identity != candidate.identity:
        return None
    return jaccard(call.tokens, candidate.tokens)


def best_tolerant(
    call: Fingerprint,
    candidates: Iterable[tuple[str, Fingerprint]],
    threshold: float = TOLERANT_THRESHOLD,
    *,
    strict_identity: bool = False,
) -> tuple[str, float] | None:
    """The best ``(key, score)`` at or above ``threshold``; ties go to the smaller key."""
    best: tuple[str, float] | None = None
    for key, candidate in sorted(candidates, key=lambda item: item[0]):
        score = tolerant_score(call, candidate, strict_identity=strict_identity)
        if score is None or score < threshold:
            continue
        if best is None or score > best[1]:
            best = (key, score)
    return best
