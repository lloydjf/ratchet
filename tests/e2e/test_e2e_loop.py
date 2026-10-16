"""Edit → replay → judge → decide → act, through the whole local
stack (simulator upstream, local process runner, process sandbox, real git)."""

from __future__ import annotations

import pytest
from e2e_support import (
    Demo,
    Hook,
    kinds,
    orchestrator_for,
    scenario_files,
    submit_operator_edit,
    verdict_after,
)

from ratchet.judge.costs import verdict_costs
from ratchet.orchestrator.bless import rebless
from ratchet.orchestrator.journal import load_state_timings
from ratchet.orchestrator.service import Orchestrator
from ratchet.orchestrator.simulate import SimulateService
from ratchet.watcher._git import run_git

pytestmark = pytest.mark.e2e

FX = "fx-expense-converter/SKILL.md"
TRIAGE = "github-triage/SKILL.md"


def _main_file(demo: Demo, path: str) -> str:
    return run_git(demo.repo.main_dir, "show", f"main:{path}").out


async def test_cosmetic_edit_passes_and_merges(demo: Demo, running: Orchestrator) -> None:
    """A wording-only edit replays clean and fast-forwards main."""
    before = demo.repo.main_head()
    edit = submit_operator_edit(demo, running, scenario_files("3"))
    verdict = await verdict_after(demo, running, edit)
    assert verdict.decision == "PASS" and verdict.action == "merged"
    assert verdict.headline.startswith("Passed: fx-expense-converter still handles all")
    assert verdict.simulated_models and verdict.action_commit
    assert demo.repo.main_head() != before
    assert _main_file(demo, FX) == scenario_files("3")[FX]
    seen = kinds(demo, edit.edit_id)
    for kind in ("edit.detected", "edit.selected", "batch.submitted", "batch.finished"):
        assert kind in seen
    assert {"pair.level0", "pair.opinion", "pair.decided", "orchestrator.state"} <= set(seen)
    keys = [s.stage for s in verdict.stages]
    assert keys[:3] == ["detected", "selecting", "replaying"] and keys[-1] == "done"
    assert set(keys) <= {"detected", "selecting", "replaying", "level0", "nano", "super"} | {
        "ultra",
        "controls",
        "drift",
        "summary",
        "done",
    }


async def test_new_close_calls_block_and_revert(demo: Demo) -> None:
    """New hard-class calls → Level 0 BLOCK → reverted, main's content unchanged,
    notification sent."""
    hook = Hook()
    hooked = demo.with_settings(ratchet_notify_webhook="https://hooks.example.com/ratchet")
    orchestrator = orchestrator_for(hooked, notify_http=hook.client())
    await orchestrator.start(watch=False)
    try:
        original = _main_file(demo, TRIAGE)
        edit = submit_operator_edit(demo, orchestrator, scenario_files("1"))
        verdict = await verdict_after(demo, orchestrator, edit)
    finally:
        await orchestrator.stop()
    assert verdict.decision == "BLOCK" and verdict.action == "reverted"
    assert verdict.reasons[0] == "level0_hard_fail"
    hard = [p for p in verdict.pairs if p.decided_by == "level0"]
    assert len(hard) == 6 and all(p.level0.hard_fail for p in hard)
    assert all(not p.opinions for p in hard)  # decided without any model
    # ADR 0023 second amendment: the parent skill never names close_issue, so the hard fail
    # stands on the fast path, without a control replay.
    assert all(p.control_run_id is None for p in verdict.pairs)
    assert not [b for b in demo.store.batches_for_edit(edit.edit_id) if b.batch_id[:5] == "ctrl_"]
    assert "pair.control" not in kinds(demo, edit.edit_id)
    assert verdict.headline == (
        "Blocked: the new github-triage would close issues in 6 of 14 replays."
    )
    assert _main_file(demo, TRIAGE) == original
    assert [p["verdict_id"] for p in hook.payloads] == [verdict.verdict_id]
    assert str(hook.payloads[0]["text"]).startswith("Ratchet BLOCK: Blocked:")
    assert "verdict.notified" in kinds(demo, verdict.verdict_id)


async def test_dropped_vat_line_blocks_via_cascade(demo: Demo, running: Orchestrator) -> None:
    """Scenario 2 → BLOCK through Super + Ultra agreement; headline names VAT."""
    service = SimulateService(demo.settings, demo.store, running)
    start = await service.start("2", "a" * 16)
    await running.wait_idle(120)
    verdict = demo.store.load_verdict(str(start.verdict_id))
    assert verdict.decision == "BLOCK" and verdict.reasons[0] == "agreed_regression"
    bad = [p for p in verdict.pairs if p.outcome == "regression"]
    assert len(bad) == 3
    for pair in bad:
        tiers = {o.tier: o.verdict for o in pair.opinions}
        assert tiers == {"nano": "regressed", "super": "regressed", "ultra": "regressed"}
    assert "VAT" in verdict.headline and verdict.headline.startswith("Blocked: expense-report")
    costs = verdict_costs(demo.store, verdict.edit_id)
    assert verdict.cost["ultra"] > 0 and costs.judge_usd_by_tier["nano"] > 0
    assert verdict.counterfactual_usd > costs.judge_usd  # the cascade saved money


async def test_world_drift_passes_and_rebless_works(demo: Demo, running: Orchestrator) -> None:
    """Scenario 3 → 4 drift pairs → PASS; drift cards carry old/new value and
    source; Re-bless replaces the golden (session-scoped in a session)."""
    session = "b" * 16
    service = SimulateService(demo.settings, demo.store, running)
    start = await service.start("3", session)
    await running.wait_idle(120)
    verdict = demo.store.load_verdict(str(start.verdict_id))
    assert verdict.decision == "PASS" and "drift" in verdict.reasons
    assert verdict.headline == (
        "Passed: 7 tasks checked; 4 changed because the world did, not the skill."
    )
    drifted = [p for p in verdict.pairs if p.outcome == "drift"]
    assert len(drifted) == 4
    card = drifted[0].drift[0]
    assert card.changed and card.attributable and card.source_url
    assert "1.08" in card.old_value and card.new_value and "1.163" in card.new_value
    pair = drifted[0]
    new_golden, archived = await rebless(
        demo.store, demo.settings, pair.golden_run_id, pair.replay_run_id, session_hash=session
    )
    assert archived == pair.golden_run_id
    copy = demo.store.load_trace(new_golden)
    assert copy.kind == "golden" and copy.archived_reason == f"session:{session}"
    assert demo.store.load_trace(archived).archived_reason is None  # shared library untouched
    shared_new, _ = await rebless(demo.store, demo.settings, pair.golden_run_id, pair.replay_run_id)
    assert demo.store.load_trace(pair.golden_run_id).archived_reason == "drift"
    reblessed = demo.store.load_trace(shared_new)
    assert "1.1630" in reblessed.final_output and reblessed.world_facts


async def test_merge_conflict_holds_with_warn(demo: Demo, running: Orchestrator) -> None:
    """Main moved under the edit → the PASS cannot merge → WARN merge_conflict."""
    repo = demo.repo
    old_main = repo.main_head()
    (repo.main_dir / "NOTES.md").write_text("operator note\n", encoding="utf-8")
    run_git(repo.main_dir, "add", "NOTES.md")
    run_git(repo.main_dir, "commit", "-q", "-m", "direct commit on main")
    edit = submit_operator_edit(demo, running, scenario_files("3"), parent=old_main)
    verdict = await verdict_after(demo, running, edit)
    assert verdict.decision == "WARN" and verdict.action == "held"
    assert verdict.reasons == ["merge_conflict"]
    assert verdict.headline.startswith("Held: skill files conflict with main")
    stages = load_state_timings(demo.store.edit_dir(edit.edit_id) / "attempt_1")
    assert stages[-1].stage == "notifying"


async def test_public_mode_with_the_process_sandbox_fails_fast(demo: Demo) -> None:
    """Regression: public mode refuses the process sandbox (ADR 0017); the edit is held
    with ``sandbox_failed`` up front instead of failing every replay one by one."""
    public = demo.with_settings(ratchet_public_mode=True)
    orchestrator = orchestrator_for(public)
    await orchestrator.start(watch=False)
    try:
        edit = submit_operator_edit(public, orchestrator, scenario_files("1"))
        verdict = await verdict_after(public, orchestrator, edit)
    finally:
        await orchestrator.stop()
    assert verdict.decision == "WARN" and verdict.reasons == ["replay_infra", "sandbox_failed"]
    assert demo.store.batches_for_edit(edit.edit_id) == []


async def test_pass_over_a_held_edit_is_held_not_merged(demo: Demo, running: Orchestrator) -> None:
    """ADR 0021: a PASS whose merge would also ship an earlier, still-held edit is held
    with ``stacked_on_held``; main does not move."""
    from ratchet import clock
    from ratchet.models import Verdict
    from ratchet.orchestrator.ids import verdict_id_for
    from ratchet.watcher.edits import build_edit

    repo = demo.repo
    before = repo.main_head()
    held_head = repo.apply_files({"github-triage/NOTE.md": "held\n"}, message="held", author="user")
    held, diff = build_edit(repo, commit=held_head, author="user")
    demo.store.save_edit(held, diff)
    demo.store.save_verdict(
        Verdict(
            verdict_id=verdict_id_for(held.edit_id),
            edit_id=held.edit_id,
            pairs=[],
            decision="WARN",
            action="held",
            narrative="Held: judges disagreed on 1 of 1 replays. Review before merging.",
            cost={"nano": 0.0, "super": 0.0, "ultra": 0.0},
            duration_s=1,
            created_at=clock.now(),
        )
    )
    edit = submit_operator_edit(demo, running, scenario_files("3"))
    verdict = await verdict_after(demo, running, edit)
    assert verdict.decision == "WARN" and verdict.action == "held"
    assert verdict.reasons == ["stacked_on_held"]
    assert verdict.headline == (
        "Held: an earlier held edit is still waiting on candidate. Review before merging."
    )
    assert repo.main_head() == before
