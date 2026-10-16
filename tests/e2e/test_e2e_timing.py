"""Timing of a 20-trace edit → verdict.

The target is ≤ 4 min on the Nebius job runner. Here the same pipeline runs on the
simulator with the local process runner, so the bound is a generous regression guard, not
that measurement; the per-stage timings are attached to the JUnit report
(``record_testsuite_property``) so CI keeps a history. The Nebius measurement itself needs
live Nebius credentials and a deployed job runner, so it is not part of this suite.
"""

from __future__ import annotations

import json
import time

import pytest
from e2e_support import Demo, orchestrator_for, submit_operator_edit, verdict_after

from ratchet.orchestrator.journal import load_state_timings

pytestmark = pytest.mark.e2e

BOUND_S = 240.0  # 4 minutes
SKILLS = ("github-triage", "expense-report", "fx-expense-converter")


def _wording_edit(demo: Demo) -> dict[str, str | None]:
    """Append one neutral sentence to three skills: ≥ 20 goldens are affected."""
    files: dict[str, str | None] = {}
    for skill in SKILLS:
        path = f"{skill}/SKILL.md"
        text = (demo.repo.candidate_dir / path).read_text(encoding="utf-8")
        files[path] = text.rstrip("\n") + "\n\nKeep replies short and plain.\n"
    return files


async def test_twenty_trace_edit_reaches_a_verdict_within_the_bound(
    demo: Demo, record_testsuite_property: pytest.RecordProperty
) -> None:
    """≥ 20 replays, edit → verdict well inside 4 minutes; stage timings kept."""
    orchestrator = orchestrator_for(demo)
    await orchestrator.start(watch=False)
    try:
        started = time.monotonic()
        edit = submit_operator_edit(demo, orchestrator, _wording_edit(demo))
        verdict = await verdict_after(demo, orchestrator, edit)
        total_s = time.monotonic() - started
    finally:
        await orchestrator.stop()
    assert len(verdict.pairs) >= 20
    assert verdict.decision in ("PASS", "WARN", "BLOCK")
    stages = load_state_timings(demo.store.edit_dir(edit.edit_id) / "attempt_1")
    timings = {s.stage: s.elapsed_ms for s in stages}
    record_testsuite_property("e2e_timing_total_s", round(total_s, 2))
    record_testsuite_property("e2e_timing_pairs", len(verdict.pairs))
    record_testsuite_property("e2e_timing_stage_ms", json.dumps(timings, sort_keys=True))
    assert {"dispatching", "collecting", "judging", "deciding"} <= set(timings)
    assert total_s < BOUND_S, f"edit → verdict took {total_s:.1f}s; stages {timings}"
