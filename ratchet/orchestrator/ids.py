"""Id helpers shared by the orchestrator and the dashboard API (design §2).

A verdict id is pre-allocated from its edit id (``edit_<hex>`` → ``verdict_<hex>``) so the
API can hand it out the moment Simulate starts; every judge call for an edit runs under
``judge_<hex>`` so its costs land on the edit (``judge.costs.verdict_costs``).
"""

from __future__ import annotations

__all__ = [
    "edit_hex",
    "verdict_id_for",
    "judge_run_id",
    "edit_id_for_verdict",
    "batch_id_for",
    "control_batch_id_for",
    "CONTROL_BATCH_PREFIX",
    "retry_batch_id_for",
    "RETRY_BATCH_PREFIX",
]

_EDIT_PREFIX = "edit_"
_VERDICT_PREFIX = "verdict_"
CONTROL_BATCH_PREFIX = "ctrl_"  # ADR 0023 control replays; never a ``batch_`` id
RETRY_BATCH_PREFIX = "retry_"  # the one automatic re-dispatch of crashed replays


def edit_hex(edit_id: str) -> str:
    """The id's suffix: ``edit_ab12`` → ``ab12``; ids without the prefix are used whole."""
    hex_part = edit_id.removeprefix(_EDIT_PREFIX)
    if not hex_part or not hex_part.replace("_", "").replace("-", "").isalnum():
        raise ValueError(f"invalid edit id {edit_id!r}")
    return hex_part


def verdict_id_for(edit_id: str) -> str:
    return f"{_VERDICT_PREFIX}{edit_hex(edit_id)}"


def edit_id_for_verdict(verdict_id: str) -> str:
    if not verdict_id.startswith(_VERDICT_PREFIX):
        raise ValueError(f"invalid verdict id {verdict_id!r}")
    return f"{_EDIT_PREFIX}{verdict_id.removeprefix(_VERDICT_PREFIX)}"


def judge_run_id(edit_id: str) -> str:
    return f"judge_{edit_hex(edit_id)}"


def batch_id_for(edit_id: str, attempt: int, index: int) -> str:
    """Deterministic per edit, attempt and position, so a resubmit is idempotent."""
    return f"batch_{edit_hex(edit_id)}_a{attempt}_{index:02d}"


def control_batch_id_for(edit_id: str, attempt: int, index: int) -> str:
    """ADR 0023: a control-replay batch. A distinct prefix keeps it out of the edit's replay
    batches (``current_batches``), so a control never stands in for the edit's replay."""
    return f"{CONTROL_BATCH_PREFIX}{edit_hex(edit_id)}_a{attempt}_{index:02d}"


def retry_batch_id_for(edit_id: str, attempt: int, index: int) -> str:
    """The edit's one retry round for crashed replays (:mod:`ratchet.orchestrator.retrying`):
    deterministic, so a restart finds the round already dispatched and never retries twice."""
    return f"{RETRY_BATCH_PREFIX}{edit_hex(edit_id)}_a{attempt}_{index:02d}"
