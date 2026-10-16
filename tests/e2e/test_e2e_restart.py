"""Restart-resume: the process dies between states, a new
orchestrator resumes from ``edits.state``, reuses what was persisted, and reaches the same
verdict as an uninterrupted run."""

from __future__ import annotations

import pytest
from e2e_support import Demo, kinds, orchestrator_for, scenario_files, submit_operator_edit

from ratchet.orchestrator import pipeline
from ratchet.orchestrator.context import EditContext
from ratchet.orchestrator.ids import verdict_id_for

pytestmark = pytest.mark.e2e


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing handles it."""


def _crash_after(state: str) -> pipeline.Handler:
    real = pipeline.HANDLERS[state]

    async def handler(ctx: EditContext) -> str:
        await real(ctx)
        raise Crash(state)

    return handler


def _crash_before(state: str) -> pipeline.Handler:
    async def handler(ctx: EditContext) -> str:
        raise Crash(state)

    return handler


async def _until_crash(demo: Demo, edit_id: str | None = None) -> str:
    orchestrator = orchestrator_for(demo)
    await orchestrator.start(watch=False)
    try:
        if edit_id is None:
            edit_id = submit_operator_edit(demo, orchestrator, scenario_files("1")).edit_id
        await orchestrator.wait_idle(120)
    finally:
        await orchestrator.stop()
    return edit_id


async def test_crashes_between_states_resume_to_the_same_verdict(
    demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(pipeline.HANDLERS, "judging", _crash_after("judging"))
    edit_id = await _until_crash(demo)
    assert demo.store.edit_state(edit_id).state == "judging"  # work done, transition lost
    judged_once = kinds(demo, edit_id).count("pair.level0")
    assert judged_once == 14

    monkeypatch.setitem(pipeline.HANDLERS, "judging", pipeline.handle_judging)
    monkeypatch.setitem(pipeline.HANDLERS, "acting", _crash_before("acting"))
    await _until_crash(demo, edit_id)
    assert demo.store.edit_state(edit_id).state == "acting"
    assert kinds(demo, edit_id).count("pair.level0") == judged_once  # pairs were reused

    monkeypatch.setitem(pipeline.HANDLERS, "acting", pipeline.handle_acting)
    await _until_crash(demo, edit_id)
    assert demo.store.edit_state(edit_id).state == "done"
    verdict = demo.store.load_verdict(verdict_id_for(edit_id))
    assert verdict.decision == "BLOCK" and verdict.action == "reverted"
    assert verdict.headline == (
        "Blocked: the new github-triage would close issues in 6 of 14 replays."
    )
    assert kinds(demo, verdict.verdict_id).count("verdict.acted") == 1
    (acted,) = demo.store.events(kinds=["verdict.acted"], ref=verdict.verdict_id)
    assert acted.payload["affected_skills"] == ["github-triage"]  # timeline titles name it
    assert kinds(demo, verdict.verdict_id).count("verdict.decided") == 1
