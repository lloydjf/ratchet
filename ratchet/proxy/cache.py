"""Model response cache for non-agent purposes (CONTRACTS §3).

Key: sha256 of ``request_hash`` + model id (+ reasoning controls, which change the answer).
Agent calls are never cached: a replay must actually re-run the model. Streams are not
cached either, because a hit is served as plain JSON.
"""

from __future__ import annotations

from datetime import timedelta

from ratchet.models import Purpose
from ratchet.store import Store
from ratchet.util import canonical_json, sha256_hex

__all__ = ["TABLE", "cacheable", "cache_key", "lookup", "store_response"]

TABLE = "response_cache"


def cacheable(purpose: Purpose, *, stream: bool) -> bool:
    return purpose != "agent" and not stream


def cache_key(request_hash: str, model: str, controls: dict[str, object] | None = None) -> str:
    material = request_hash + model
    if controls:
        material += canonical_json(controls)
    return sha256_hex(material)


def lookup(store: Store, key: str, ttl_days: int) -> dict[str, object] | None:
    value = store.cache_get(TABLE, key, ttl=timedelta(days=ttl_days))
    if not isinstance(value, dict):
        return None
    return {str(k): v for k, v in value.items()}


def store_response(store: Store, key: str, model: str, response: dict[str, object]) -> None:
    store.cache_put(TABLE, key, response, model=model)
