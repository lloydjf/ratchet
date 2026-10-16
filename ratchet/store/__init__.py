"""ratchet-store: trace store, cassettes, verdicts, costs and the events spine.

Usage::

    store = Store.open(settings.ratchet_data_dir)   # creates dirs, runs migrations
    store.save_trace(trace); store.emit("trace.blessed", trace.run_id, {...})

The Store is synchronous; async callers wrap calls in ``asyncio.to_thread``.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from ratchet.models import Cassette
from ratchet.store import db
from ratchet.store.base import write_json_atomic
from ratchet.store.ledger import CostRow, LedgerStore, TierTotals
from ratchet.store.records import (
    BatchRow,
    EditState,
    RecordStore,
    SelectedTrace,
    SessionRow,
    VerdictRow,
)
from ratchet.store.traces import TraceRow, TraceStore

__all__ = [
    "Store",
    "CostRow",
    "TierTotals",
    "TraceRow",
    "EditState",
    "SelectedTrace",
    "BatchRow",
    "VerdictRow",
    "SessionRow",
]

_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class Store(TraceStore, RecordStore, LedgerStore):
    @classmethod
    def open(cls, data_dir: Path) -> Store:
        data_dir.mkdir(parents=True, exist_ok=True)
        for sub in ("traces", "cassettes", "edits", "batches", "verdicts", "tavily"):
            (data_dir / sub).mkdir(exist_ok=True)
        store = cls(data_dir)
        db.migrate(store.db_path)
        return store

    # -- cassettes (data/cassettes/<tool>/<args_hash>.json) ------------------------------------
    def cassette_path(self, tool: str, key: str, root: Path | None = None) -> Path:
        if not _TOOL_NAME.match(tool) or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError(f"bad cassette address {tool!r}/{key!r}")
        return (root or self.cassettes_dir) / tool / f"{key}.json"

    def save_cassette(self, key: str, cassette: Cassette, root: Path | None = None) -> Path:
        path = self.cassette_path(cassette.tool, key, root)
        write_json_atomic(path, cassette)
        return path

    def load_cassette(self, tool: str, key: str, root: Path | None = None) -> Cassette | None:
        path = self.cassette_path(tool, key, root)
        if not path.exists():
            return None
        return self.load_model(path, Cassette)

    def copy_cassettes(self, addresses: list[tuple[str, str]], dest_root: Path) -> int:
        """Copy the named cassettes into ``dest_root`` (a batch's cassette set)."""
        copied = 0
        for tool, key in sorted(set(addresses)):
            src = self.cassette_path(tool, key)
            if src.exists():
                dst = self.cassette_path(tool, key, dest_root)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, dst)
                copied += 1
        return copied

    # -- retention (`ratchet purge --before <date>`) -------------------------------------------
    def purge(self, before: datetime, *, include_golden: bool = False) -> int:
        from ratchet import clock

        kinds = ("live", "replay", "golden") if include_golden else ("live", "replay")
        marks = ",".join("?" for _ in kinds)
        rows = self.query(
            f"SELECT run_id FROM traces WHERE created_at < ? AND kind IN ({marks})",
            (clock.iso(before), *kinds),
        )
        run_ids = [str(r["run_id"]) for r in rows]
        with self.tx() as conn:
            conn.executemany("DELETE FROM traces WHERE run_id = ?", [(r,) for r in run_ids])
            conn.executemany("DELETE FROM trace_skills WHERE run_id = ?", [(r,) for r in run_ids])
        for run_id in run_ids:
            shutil.rmtree(self.trace_dir(run_id), ignore_errors=True)
        return len(run_ids)

    def purge_cassettes(self, before: datetime) -> int:
        """Delete cassettes recorded before ``before`` that no remaining trace references
        (``ratchet purge``); run it after :meth:`purge`."""
        from ratchet.store.retention import purge_cassettes

        return purge_cassettes(self.cassettes_dir, self.data_dir / "traces", before)
