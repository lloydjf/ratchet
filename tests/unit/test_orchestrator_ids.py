"""Id helpers (design §2)."""

from __future__ import annotations

import pytest

from ratchet.orchestrator.ids import (
    batch_id_for,
    edit_id_for_verdict,
    judge_run_id,
    verdict_id_for,
)


def test_verdict_and_judge_ids_derive_from_the_edit_id() -> None:
    assert verdict_id_for("edit_ab12cd") == "verdict_ab12cd"
    assert judge_run_id("edit_ab12cd") == "judge_ab12cd"
    assert edit_id_for_verdict("verdict_ab12cd") == "edit_ab12cd"


def test_batch_ids_are_deterministic_per_edit_attempt_and_index() -> None:
    assert batch_id_for("edit_ab12", 2, 3) == "batch_ab12_a2_03"
    assert batch_id_for("edit_ab12", 2, 3) == batch_id_for("edit_ab12", 2, 3)


@pytest.mark.parametrize("bad", ["", "edit_", "edit_../x", "edit_a/b"])
def test_invalid_edit_ids_are_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        verdict_id_for(bad)


def test_verdict_ids_need_their_prefix() -> None:
    with pytest.raises(ValueError):
        edit_id_for_verdict("edit_ab12")
