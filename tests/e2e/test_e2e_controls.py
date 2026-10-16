"""ADR 0023 control replays through the whole local stack (simulator upstream, local process
runner, real git): a contested pair gets one more replay with the parent skill.

The simulator is deterministic, so nondeterminism is scripted here: the control batches'
imported replays are swapped for the edit's own replay of the same golden, as if the
unchanged skill had produced the same different answer (the bench E6 failure mode)."""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e_support import Demo, kinds, submit_operator_edit, verdict_after

from ratchet import copy
from ratchet.models import ReplayResult, ToolCall, Trace
from ratchet.orchestrator import replaying
from ratchet.orchestrator.ids import CONTROL_BATCH_PREFIX
from ratchet.orchestrator.service import Orchestrator
from ratchet.orchestrator.simulate import SimulateService
from ratchet.store import Store

pytestmark = pytest.mark.e2e


def _edit_replays(store: Store, edit_id: str) -> dict[str, Trace]:
    found: dict[str, Trace] = {}
    for row in store.batches_for_edit(edit_id):
        result = store.load_batch_result(row.batch_id)
        if row.batch_id.startswith("batch_") and result is not None:
            found.update({t.golden_of: t for t in result.replays if t.golden_of})
    return found


def _flaky_controls(monkeypatch: pytest.MonkeyPatch) -> None:
    real = replaying.import_batch_output

    def flaky(store: Store, out: Path, **kw: object) -> ReplayResult:
        result = real(store, out, **kw)  # type: ignore[arg-type]
        if not result.batch_id.startswith(CONTROL_BATCH_PREFIX):
            return result
        edit_id = store.load_batch_spec(result.batch_id).edit_id
        theirs = _edit_replays(store, edit_id)
        swapped = []
        for control in result.replays:
            same = theirs.get(str(control.golden_of))
            if same is not None:
                update = {"steps": same.steps, "final_output": same.final_output}
                control = control.model_copy(update=update)
                store.save_trace(control)
            swapped.append(control)
        result = result.model_copy(update={"replays": swapped})
        store.save_batch_result(result)
        return result

    monkeypatch.setattr(replaying, "import_batch_output", flaky)


async def test_a_regression_the_unchanged_skill_repeats_is_held_not_blocked(
    demo: Demo, running: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flaky_controls(monkeypatch)
    service = SimulateService(demo.settings, demo.store, running)
    start = await service.start("2", "c" * 16)
    await running.wait_idle(120)
    verdict = demo.store.load_verdict(str(start.verdict_id))
    assert verdict.decision == "PASS" and verdict.action == "merged"
    assert "agreed_regression" not in verdict.reasons
    held = [p for p in verdict.pairs if p.decided_by == "control"]
    assert len(held) == 3 and not [p for p in verdict.pairs if p.outcome == "regression"]
    for pair in held:
        assert pair.outcome == "pass" and pair.error_code is None
        assert pair.reason == copy.explain_nondeterminism()
        assert pair.control_outcome == "diverges" and pair.control_run_id
        control = demo.store.load_trace(pair.control_run_id)
        assert control.golden_of == pair.golden_run_id and control.kind == "replay"
        assert {o.tier for o in pair.opinions} == {"nano", "super", "ultra"}  # kept
    seen = kinds(demo, verdict.edit_id)
    assert seen.count("pair.control") == 3
    assert "controls" in [s.stage for s in verdict.stages]


async def test_a_real_regression_still_blocks_when_its_controls_agree(
    demo: Demo, running: Orchestrator
) -> None:
    """Scenario 2 with controls: the unchanged skill keeps the VAT line, so the block stands."""
    service = SimulateService(demo.settings, demo.store, running)
    start = await service.start("2", "d" * 16)
    await running.wait_idle(120)
    verdict = demo.store.load_verdict(str(start.verdict_id))
    assert verdict.decision == "BLOCK" and verdict.reasons[0] == "agreed_regression"
    bad = [p for p in verdict.pairs if p.outcome == "regression"]
    assert len(bad) == 3
    assert all(p.control_outcome == "agrees" and p.control_run_id for p in bad)
    clean = [p for p in verdict.pairs if p.outcome == "pass"]
    assert clean and all(p.control_run_id is None for p in clean)  # pass: no control, no cost
    control_batches = [
        b for b in demo.store.batches_for_edit(verdict.edit_id) if b.batch_id.startswith("ctrl_")
    ]
    specs = [demo.store.load_batch_spec(b.batch_id) for b in control_batches]
    replayed = sorted(g for s in specs for g in s.golden_run_ids)
    assert replayed == sorted(p.golden_run_id for p in bad)
    assert not any(b.startswith("ctrl_") for b in verdict.batch_ids)


async def test_controls_off_keeps_the_single_replay_verdict(demo: Demo) -> None:
    from e2e_support import orchestrator_for

    off = demo.with_settings(ratchet_feature_control_replays=False)
    orchestrator = orchestrator_for(off)
    await orchestrator.start(watch=False)
    try:
        service = SimulateService(off.settings, off.store, orchestrator)
        start = await service.start("2", "e" * 16)
        await orchestrator.wait_idle(120)
    finally:
        await orchestrator.stop()
    verdict = demo.store.load_verdict(str(start.verdict_id))
    assert verdict.decision == "BLOCK"
    assert all(p.control_run_id is None for p in verdict.pairs)
    assert "pair.control" not in kinds(demo, verdict.edit_id)


def _comments(trace: Trace) -> list[ToolCall]:
    return [s for s in trace.steps if isinstance(s, ToolCall) and s.tool == "github.comment"]


async def test_an_instructed_hard_call_the_unchanged_skill_repeats_passes_as_noisy(
    demo: Demo, running: Orchestrator
) -> None:
    """ADR 0023 second amendment (live E6): the golden skipped a question comment the
    unchanged skill tells the agent to post; the replay and its control both post it, so the
    Level 0 hard fail is agent variance: PASS, the pair marked noisy, the finding kept."""
    store = demo.store
    goldens = [store.load_trace(r.run_id) for r in store.list_traces(skill="github-triage")]
    target = next(g for g in goldens if _comments(g))
    steps = [s for s in target.steps if s not in _comments(target)]
    store.save_trace(target.model_copy(update={"steps": steps}))  # the golden that skipped it
    edit = submit_operator_edit(demo, running, {"github-triage/NOTE.md": "triage notes\n"})
    verdict = await verdict_after(demo, running, edit)
    assert verdict.decision == "PASS" and verdict.action == "merged"
    assert "level0_hard_fail" not in verdict.reasons
    (noisy,) = [p for p in verdict.pairs if p.decided_by == "control"]
    assert noisy.golden_run_id == target.run_id and noisy.outcome == "pass"
    assert noisy.control_outcome == "diverges" and noisy.control_run_id
    assert noisy.reason == copy.explain_nondeterminism()
    assert noisy.level0.hard_fail  # the Level 0 finding stays attached as evidence
    assert {c.tool for c in noisy.level0.new_calls} == {"github.comment"}
    assert _comments(store.load_trace(noisy.control_run_id))  # the unchanged skill posted it
    ctrl = [b for b in store.batches_for_edit(edit.edit_id) if b.batch_id.startswith("ctrl_")]
    replayed = [g for b in ctrl for g in store.load_batch_spec(b.batch_id).golden_run_ids]
    assert replayed == [target.run_id]
    assert kinds(demo, edit.edit_id).count("pair.control") == 1
