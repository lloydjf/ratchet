"""One automatic retry for a crashed replay through the whole local stack (simulator
upstream, local process runner, real git).

The simulator never crashes, so the crash is scripted: the first import of the edit's own
batches drops one replay and records it ``harness_failed``, the way a transient agent crash
does. The retry round replays that golden again and the edit passes."""

from __future__ import annotations

from pathlib import Path

import pytest
from e2e_support import Demo, scenario_files, submit_operator_edit, verdict_after

from ratchet.models import ReplayResult
from ratchet.orchestrator import replaying
from ratchet.orchestrator.ids import RETRY_BATCH_PREFIX
from ratchet.orchestrator.service import Orchestrator
from ratchet.store import Store

pytestmark = pytest.mark.e2e


def _crash_once(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    real = replaying.import_batch_output
    crashed: list[str] = []

    def crashing(store: Store, out: Path, **kw: object) -> ReplayResult:
        result = real(store, out, **kw)  # type: ignore[arg-type]
        if crashed or not result.batch_id.startswith("batch_") or not result.replays:
            return result
        lost = result.replays[0]
        crashed.append(str(lost.golden_of))
        update = {
            "replays": result.replays[1:],
            "failed_goldens": {**result.failed_goldens, str(lost.golden_of): "harness_failed"},
            "status": "partial",
        }
        result = result.model_copy(update=update)
        store.save_batch_result(result)
        return result

    monkeypatch.setattr(replaying, "import_batch_output", crashing)
    return crashed


async def test_a_crashed_replay_is_retried_once_and_the_edit_passes(
    demo: Demo, running: Orchestrator, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashed = _crash_once(monkeypatch)
    edit = submit_operator_edit(demo, running, scenario_files("3"))
    verdict = await verdict_after(demo, running, edit)
    assert len(crashed) == 1
    assert verdict.decision == "PASS" and verdict.action == "merged"
    assert "replay_failed" not in verdict.reasons
    assert not [p for p in verdict.pairs if p.outcome == "failed"]
    retries = [b for b in verdict.batch_ids if b.startswith(RETRY_BATCH_PREFIX)]
    assert len(retries) == 1
    assert demo.store.load_batch_spec(retries[0]).golden_run_ids == crashed
    (pair,) = [p for p in verdict.pairs if p.golden_run_id == crashed[0]]
    assert demo.store.load_trace(pair.replay_run_id).golden_of == crashed[0]
    submitted = demo.store.events(ref=edit.edit_id, kinds=["batch.submitted"], limit=100)
    assert [e.payload["batch_id"] for e in submitted if e.payload["status"] == "retry"] == retries


async def test_retry_off_keeps_the_crash_as_a_failed_pair(
    demo: Demo, monkeypatch: pytest.MonkeyPatch
) -> None:
    from e2e_support import orchestrator_for

    _crash_once(monkeypatch)
    off = demo.with_settings(ratchet_replay_retry_once=False)
    orchestrator = orchestrator_for(off)
    await orchestrator.start(watch=False)
    try:
        edit = submit_operator_edit(off, orchestrator, scenario_files("3"))
        verdict = await verdict_after(off, orchestrator, edit)
    finally:
        await orchestrator.stop()
    assert verdict.decision == "WARN" and "replay_failed" in verdict.reasons
    assert not [b for b in verdict.batch_ids if b.startswith(RETRY_BATCH_PREFIX)]
