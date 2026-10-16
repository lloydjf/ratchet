"""Deterministic hashed embeddings for the simulator (``POST /v1/embeddings``, ADR 0018).

Each text becomes a bag of lower-cased word tokens and word bigrams; every feature is hashed
(sha256) to a bucket and a sign, weighted by ``1 + log(count)``, and the vector is
L2-normalized and rounded to 6 decimals. Similar texts get similar vectors, so trigger
neighbourhood selection behaves sensibly offline. Not a real embedding model.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter

from ratchet.sim.text import words

__all__ = ["DEFAULT_DIMENSIONS", "MAX_DIMENSIONS", "embed", "embed_all"]

DEFAULT_DIMENSIONS = 256
MAX_DIMENSIONS = 4096


def _features(text: str) -> Counter[str]:
    tokens = words(text)
    feats = Counter(tokens)
    feats.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False))
    return feats


def embed(text: str, dimensions: int = DEFAULT_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for feature, count in sorted(_features(text).items()):
        digest = hashlib.sha256(feature.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        weight = 1.0 + math.log(count)
        vector[bucket] += sign * (weight if " " not in feature else 0.5 * weight)
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return vector
    return [round(v / norm, 6) for v in vector]


def embed_all(texts: list[str], dimensions: int = DEFAULT_DIMENSIONS) -> list[list[float]]:
    size = max(1, min(dimensions, MAX_DIMENSIONS))
    return [embed(text, size) for text in texts]
