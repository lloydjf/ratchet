"""Shared base for the Store mixins: paths, atomic JSON writes, row helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from ratchet.store import db

__all__ = ["StoreBase", "write_json_atomic", "read_json"]

M = TypeVar("M", bound=BaseModel)


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if isinstance(payload, BaseModel):
        text = payload.model_dump_json(indent=2)
    else:
        text = json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False)
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class StoreBase:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.db_path = data_dir / "ratchet.db"

    # -- storage layout --------------------------------------------------------------
    def trace_dir(self, run_id: str) -> Path:
        _check_id(run_id)
        return self.data_dir / "traces" / run_id

    def edit_dir(self, edit_id: str) -> Path:
        _check_id(edit_id)
        return self.data_dir / "edits" / edit_id

    def batch_dir(self, batch_id: str) -> Path:
        _check_id(batch_id)
        return self.data_dir / "batches" / batch_id

    def verdict_path(self, verdict_id: str) -> Path:
        _check_id(verdict_id)
        return self.data_dir / "verdicts" / f"{verdict_id}.json"

    @property
    def cassettes_dir(self) -> Path:
        return self.data_dir / "cassettes"

    @property
    def tavily_dir(self) -> Path:
        return self.data_dir / "tavily"

    # -- db helpers ---------------------------------------------------------------------
    @contextmanager
    def tx(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with db.transaction(self.db_path, immediate=immediate) as conn:
            yield conn

    def query(self, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
        conn = db.connect(self.db_path)
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()

    def query_one(self, sql: str, params: tuple[object, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def load_model(path: Path, model: type[M]) -> M:
        return model.model_validate_json(path.read_text(encoding="utf-8"))


def _check_id(value: str) -> None:
    """Ids become path segments; refuse anything that could escape the data dir."""
    if not value or "/" in value or "\\" in value or value.startswith(".") or "\x00" in value:
        raise ValueError(f"invalid id {value!r}")
