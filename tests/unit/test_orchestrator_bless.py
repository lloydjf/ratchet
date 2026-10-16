"""Bless / rebless / unbless / criteria through the simulator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import fixture_trace, make_settings, make_verdict, open_store, pair  # noqa: E402

from ratchet.errors import ConflictError, ForbiddenError  # noqa: E402
from ratchet.models import Cassette, DriftCheck, SkillEdit, ToolCall  # noqa: E402
from ratchet.orchestrator.bless import (  # noqa: E402
    ARCHIVED_DRIFT_FILE,
    bless,
    rebless,
    set_criteria,
    unbless,
)
from ratchet.orchestrator.ids import verdict_id_for  # noqa: E402
from ratchet.store.records import SelectedTrace  # noqa: E402

EMAIL = "jun@acme.example.com"


def _live(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = make_settings(tmp_path)
    store = open_store(settings)
    trace = fixture_trace(
        "d1_fx_drift",
        kind="live",
        run_id="run_live1",
        world_facts=[],
        success_criteria=[],
        blessed_at=None,
        task_input=f"Convert the USD receipts to EUR and mail {EMAIL}.",
    )
    store.save_trace(trace)
    return settings, store, trace


async def test_bless_tokenizes_pii_enriches_and_marks_golden(tmp_path: Path) -> None:
    settings, store, _ = _live(tmp_path)
    golden = await bless(store, settings, "run_live1")
    assert golden.kind == "golden" and golden.blessed_at is not None
    assert EMAIL not in golden.task_input and "<EMAIL_1>" in golden.task_input
    assert golden.world_dependent and golden.success_criteria  # finance.* is a world tool
    assert store.load_trace("run_live1").kind == "golden"
    (event,) = store.events(kinds=["trace.blessed"])
    assert event.payload["n_facts"] == len(golden.world_facts)
    assert (await bless(store, settings, "run_live1")).blessed_at == golden.blessed_at


async def test_public_mode_refuses_bless_unbless_and_criteria(tmp_path: Path) -> None:
    _, store, _ = _live(tmp_path)
    public = make_settings(tmp_path, ratchet_public_mode=True)
    with pytest.raises(ForbiddenError):
        await bless(store, public, "run_live1")
    with pytest.raises(ForbiddenError):
        unbless(store, public, "run_live1")
    with pytest.raises(ForbiddenError):
        set_criteria(store, public, "run_live1", ["x"])


async def test_only_live_runs_can_be_blessed(tmp_path: Path) -> None:
    settings, store, trace = _live(tmp_path)
    store.save_trace(trace.model_copy(update={"kind": "replay", "golden_of": "run_x"}))
    with pytest.raises(ConflictError):
        await bless(store, settings, "run_live1")


async def test_unbless_and_criteria_edit_a_golden(tmp_path: Path) -> None:
    settings, store, _ = _live(tmp_path)
    await bless(store, settings, "run_live1")
    updated = set_criteria(store, settings, "run_live1", [" Shows the EUR total ", ""])
    assert updated.success_criteria == ["Shows the EUR total"]
    assert unbless(store, settings, "run_live1").kind == "live"
    with pytest.raises(ConflictError):
        set_criteria(store, settings, "run_live1", ["x"])


def _pair(tmp_path: Path, overlay: Path | None = None):  # type: ignore[no-untyped-def]
    settings = make_settings(tmp_path)
    store = open_store(settings)
    golden = fixture_trace("d1_fx_drift", run_id="run_gold", kind="golden")
    replay = fixture_trace(
        "d1_fx_drift",
        "replay",
        run_id="run_rep",
        golden_of="run_gold",
        env=golden.env.model_copy(update={"world_ref": str(overlay) if overlay else None}),
    )
    steps = [
        s.model_copy(update={"cassette_key": "ab" * 32}) if isinstance(s, ToolCall) else s
        for s in replay.steps
    ]
    replay = replay.model_copy(update={"steps": steps})
    store.save_trace(golden)
    store.save_trace(replay)
    return settings, store, golden, replay


async def test_rebless_replaces_the_golden_and_promotes_overlay_cassettes(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay"
    settings, store, golden, replay = _pair(tmp_path, overlay)
    call = next(s for s in replay.steps if isinstance(s, ToolCall) and s.tool == "finance.rate")
    assert call.cassette_key is not None
    store.save_cassette(
        call.cassette_key,
        Cassette(
            tool=call.tool,
            args_norm=call.args_norm,
            response={"result": {"rate": 0.95}},
            recorded_at=golden.created_at,
            side_effect=call.side_effect,
        ),
        root=overlay,
    )
    new, archived = await rebless(store, settings, "run_gold", "run_rep")
    assert archived == "run_gold"
    assert store.load_trace("run_gold").archived_reason == "drift"
    fresh = store.load_trace(new)
    assert fresh.kind == "golden" and fresh.golden_of is None and fresh.env == golden.env
    assert fresh.final_output == replay.final_output and fresh.blessed_at is not None
    promoted = store.load_cassette(call.tool, call.cassette_key)
    assert promoted is not None and promoted.response == {"result": {"rate": 0.95}}
    assert store.events(kinds=["pair.reblessed"])[0].payload["new_golden"] == new


async def test_a_session_rebless_is_scoped_and_allowed_in_public_mode(tmp_path: Path) -> None:
    _, store, _, _ = _pair(tmp_path)
    public = make_settings(tmp_path, ratchet_public_mode=True)
    with pytest.raises(ForbiddenError):
        await rebless(store, public, "run_gold", "run_rep")
    new, archived = await rebless(store, public, "run_gold", "run_rep", session_hash="s" * 16)
    assert store.load_trace(new).archived_reason == "session:" + "s" * 16
    assert store.load_trace(archived).archived_reason is None
    assert new not in {r.run_id for r in store.list_traces(kind="golden")}


async def test_rebless_checks_the_pair_belongs_together(tmp_path: Path) -> None:
    settings, store, _, replay = _pair(tmp_path)
    store.save_trace(replay.model_copy(update={"golden_of": "run_other"}))
    with pytest.raises(ConflictError):
        await rebless(store, settings, "run_gold", "run_rep")


async def test_rebless_archives_the_pairs_drift_checks_with_the_old_golden(
    tmp_path: Path,
) -> None:
    """Rebless: the old golden is archived with ``drift`` *and the drift checks*."""
    settings, store, golden, _ = _pair(tmp_path)
    check = DriftCheck(
        fact_id="f1",
        query="usd eur rate",
        tavily_results_ref="tavily/abc.json",
        changed=True,
        old_value="0.92",
        new_value="0.95",
        confidence=0.9,
    )
    edit = SkillEdit(
        edit_id="edit_" + "c" * 12,
        commit="0" * 40,
        parent="0" * 40,
        files=[],
        diff_ref="",
        affected_skills=[],
        author="user",
        detected_at=golden.created_at,
    )
    store.save_edit(edit, "")
    store.save_selection(edit.edit_id, [SelectedTrace("run_gold", "direct", None)])
    found = pair("run_gold", "run_rep", "drift").model_copy(update={"drift": [check]})
    store.save_verdict(make_verdict(edit, pairs=[found], decision="PASS", action="merged"))
    new, _ = await rebless(store, settings, "run_gold", "run_rep")
    sidecar = json.loads((store.trace_dir("run_gold") / ARCHIVED_DRIFT_FILE).read_text())
    assert sidecar["verdict_id"] == verdict_id_for(edit.edit_id)
    assert sidecar["new_golden"] == new and sidecar["drift"][0]["new_value"] == "0.95"
    event = store.events(kinds=["pair.reblessed"])[0]
    assert event.payload["drift_fact_ids"] == ["f1"]
