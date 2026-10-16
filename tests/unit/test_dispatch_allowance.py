"""Batches carry the spending allowance left at dispatch (control-plane ledger).

The worker's sidecar proxy records into an empty scratch store, so the caps it would check
there never trip; the orchestrator stamps ``ReplayBatch.budget_allowance_usd`` instead,
shared equally by the batches of one dispatch.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from orch_fakes import fixture_trace, make_settings, open_store

from ratchet.orchestrator.replaying import _plan
from ratchet.store import CostRow
from ratchet.store.records import SelectedTrace


def test_dispatch_stamps_each_batch_with_its_share_of_the_remaining_allowance(
    tmp_path: Path,
) -> None:
    settings = make_settings(
        tmp_path, ratchet_daily_usd_cap=10.0, ratchet_batch_size=1, ratchet_bench_usd_cap=12.0
    )
    store = open_store(settings)
    ids = []
    for i, name in enumerate(("r1_logic", "r2_dropped_field")):
        trace = fixture_trace(name, run_id=f"run_g{i}")
        store.save_trace(trace)
        ids.append(trace.run_id)
    store.save_selection("edit_1", [SelectedTrace(r, "direct", None) for r in ids])
    store.record_cost(CostRow("c1", "judge_x", None, "super", "m", 1, 1, 8.0, "adjudicate"))
    ctx = SimpleNamespace(
        settings=settings,
        store=store,
        edit_id="edit_1",
        edit=SimpleNamespace(commit="abc", session_hash=None),
        options=SimpleNamespace(attempt=1, world_ref=None),
    )

    specs = _plan(ctx, tmp_path / "skills.tar.zst")  # type: ignore[arg-type]

    assert len(specs) == 2
    for spec in specs:
        assert spec.budget_allowance_usd is not None
        assert spec.budget_allowance_usd["daily"] == pytest.approx(1.0)  # (10 - 8) / 2
        assert spec.budget_allowance_usd["bench"] == pytest.approx(6.0)
