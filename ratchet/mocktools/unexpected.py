"""The unexpected-side-effect log (``GET /log``, ``ReplayResult.unexpected_by_run``).

Replay-mode cassette misses whose side effect is not ``read``/``none`` land here: blocked
hard-class calls and synthetic-success ``write_reversible`` calls. Kept in memory for
``GET /log`` and appended to ``traces/<run_id>/unexpected.jsonl`` (one ``ToolCall`` JSON
per line) so a worker can collect it even after the server is gone.
"""

from __future__ import annotations

import threading

from ratchet.models import ToolCall
from ratchet.store import Store

__all__ = ["UnexpectedLog", "UNEXPECTED_FILE", "read_unexpected"]

UNEXPECTED_FILE = "unexpected.jsonl"


class UnexpectedLog:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._lock = threading.Lock()
        self._by_run: dict[str, list[ToolCall]] = {}

    def add(self, run_id: str, call: ToolCall) -> None:
        with self._lock:
            self._by_run.setdefault(run_id, []).append(call)
            path = self.store.trace_dir(run_id) / UNEXPECTED_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(call.model_dump_json() + "\n")

    def by_run(self, run_id: str | None = None) -> dict[str, list[ToolCall]]:
        with self._lock:
            if run_id is not None:
                return {run_id: list(self._by_run.get(run_id, []))}
            return {k: list(v) for k, v in self._by_run.items()}


def read_unexpected(store: Store, run_id: str) -> list[ToolCall]:
    """The persisted log of one run (empty if the run had no unexpected calls)."""
    path = store.trace_dir(run_id) / UNEXPECTED_FILE
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ToolCall.model_validate_json(line) for line in lines if line.strip()]
