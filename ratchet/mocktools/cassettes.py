"""Cassette lookup and recording for the mock tool server.

Layout: ``<root>/<tool>/<cassette_key>.json`` holding a :class:`~ratchet.models.Cassette`.
A cassette's ``response`` is ``{"result": ...}`` for a successful call or
``{"error": {"code", "message"}, "status": <http>}`` for a recorded tool error; any other
shape is served as the result itself (hand-authored cassettes).

Replay lookup order: exact key in the overlay (world-dependent tools only), exact key in
the main root, tolerant match in the overlay, tolerant match in the main root. Tolerant
matching is skipped for hard side effects and identity-checked for reversible writes
(``matching.tolerance_for``). Repeated
identical calls in one run are answered from a per-run, per-key queue: ``response``, then
each of ``later_responses``, then the last one again. A corrupted cassette file is a miss.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from ratchet.log import get_logger
from ratchet.mocktools.matching import Fingerprint, Tolerance, best_tolerant, fingerprint
from ratchet.models import Cassette, SideEffectClass
from ratchet.store import Store

__all__ = ["CassetteBook", "Lookup", "RecordAction"]

RecordAction = Literal["created", "refreshed", "appended", "kept"]
_log = get_logger("mocktools")


@dataclass(frozen=True)
class Lookup:
    cassette: Cassette
    key: str  # key of the cassette that answered (differs from the call's on a tolerant hit)
    root: Path
    kind: Literal["hit", "tolerant_hit"]
    layer: Literal["overlay", "main"]
    score: float = 1.0


@dataclass
class _ToolIndex:
    stamp: tuple[int, int]
    prints: dict[str, Fingerprint]


class CassetteBook:
    def __init__(self, store: Store, root: Path, overlay_root: Path | None = None) -> None:
        self.store = store
        self.root = root
        self.overlay_root = overlay_root
        self._lock = threading.RLock()
        self._indexes: dict[tuple[Path, str], _ToolIndex] = {}
        self._served: dict[tuple[str, Path, str], int] = {}
        self._recorded: dict[tuple[str, str], int] = {}
        self._owned: set[tuple[str, str]] = set()

    # -- reading ----------------------------------------------------------------------
    def load(self, tool: str, key: str, root: Path) -> Cassette | None:
        """The cassette at ``(tool, key)`` under ``root``; None if absent or unreadable."""
        try:
            return self.store.load_cassette(tool, key, root)
        except (ValidationError, ValueError, UnicodeDecodeError, OSError):
            _log.warning("cassette.corrupt", tool=tool, cassette_key=key)
            return None

    def _index(self, tool: str, root: Path) -> dict[str, Fingerprint]:
        directory = root / tool
        if not directory.is_dir():
            return {}
        stat = directory.stat()
        stamp = (stat.st_mtime_ns, sum(1 for _ in directory.glob("*.json")))
        cached = self._indexes.get((root, tool))
        if cached is not None and cached.stamp == stamp:
            return cached.prints
        prints: dict[str, Fingerprint] = {}
        for path in sorted(directory.glob("*.json")):
            cassette = self.load(tool, path.stem, root) if len(path.stem) == 64 else None
            if cassette is not None and cassette.tool == tool:
                prints[path.stem] = fingerprint(cassette.args_norm)
        self._indexes[(root, tool)] = _ToolIndex(stamp, prints)
        return prints

    def _layers(self, world_dependent: bool) -> list[tuple[Literal["overlay", "main"], Path]]:
        layers: list[tuple[Literal["overlay", "main"], Path]] = []
        if world_dependent and self.overlay_root is not None:
            layers.append(("overlay", self.overlay_root))
        layers.append(("main", self.root))
        return layers

    def lookup(
        self,
        tool: str,
        args_norm: Mapping[str, object],
        key: str,
        *,
        world_dependent: bool,
        tolerance: Tolerance = "full",
    ) -> Lookup | None:
        layers = self._layers(world_dependent)
        with self._lock:
            for layer, root in layers:
                exact = self.load(tool, key, root)
                if exact is not None:
                    return Lookup(exact, key, root, "hit", layer)
            if tolerance == "exact":
                return None
            call = fingerprint(args_norm)
            for layer, root in layers:
                index = self._index(tool, root)
                found = best_tolerant(call, index.items(), strict_identity=tolerance == "identity")
                if found is None:
                    continue
                cassette = self.load(tool, found[0], root)
                if cassette is not None:
                    return Lookup(cassette, found[0], root, "tolerant_hit", layer, found[1])
        return None

    def next_response(self, run_id: str, found: Lookup) -> dict[str, object]:
        """Ordering: recorded order per run and key, then repeat the last."""
        responses = [found.cassette.response, *found.cassette.later_responses]
        with self._lock:
            slot = (run_id, found.root, found.key)
            index = self._served.get(slot, 0)
            self._served[slot] = index + 1
        return responses[min(index, len(responses) - 1)]

    # -- recording --------------------------------------------------------------------
    def record(
        self,
        *,
        run_id: str,
        tool: str,
        key: str,
        args_norm: dict[str, object],
        args_raw_hash: str,
        response: dict[str, object],
        side_effect: SideEffectClass,
        recorded_at: datetime,
        refresh: bool,
    ) -> RecordAction:
        """Store a live response. First call per run creates (or, with refresh, overwrites);
        later identical calls in the same run append to ``later_responses``."""
        with self._lock:
            slot = (run_id, key)
            count = self._recorded.get(slot, 0) + 1
            self._recorded[slot] = count
            existing = self.load(tool, key, self.root)
            if existing is not None and count > 1 and slot in self._owned:
                if len(existing.later_responses) >= count - 1:
                    return "kept"
                existing.later_responses.append(response)
                self.store.save_cassette(key, existing, self.root)
                return "appended"
            if existing is not None and not (refresh and count == 1):
                return "kept"
            fresh = Cassette(
                tool=tool,
                args_norm=args_norm,
                response=response,
                recorded_at=recorded_at,
                side_effect=side_effect,
                args_raw_hash=args_raw_hash,
            )
            self.store.save_cassette(key, fresh, self.root)
            self._owned.add(slot)
            return "created" if existing is None else "refreshed"
