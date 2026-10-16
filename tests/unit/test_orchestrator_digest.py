"""The weekly digest through the simulator."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import commit_edit, make_settings, make_verdict, open_store, seeded_repo  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.orchestrator.digest import latest, run_digest  # noqa: E402


async def test_run_digest_writes_the_file_row_and_event(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    assert latest(store) is None
    repo = seeded_repo(settings)
    edit, diff = commit_edit(repo, {"github-triage/NOTE.md": "x\n"})
    store.save_edit(edit, diff)
    store.save_verdict(
        make_verdict(edit, decision="BLOCK", action="reverted", created_at=clock.now())
    )
    digest = await run_digest(store, settings)
    assert digest.narrative and digest.simulated_models
    assert digest.blocked == digest.verdict_ids == [edit.edit_id.replace("edit_", "verdict_")]
    assert latest(store) == digest
    (event,) = store.events(kinds=["digest.written"])
    assert event.payload["verdict_ids"] == digest.verdict_ids


async def test_digest_carries_the_product_metrics_table(tmp_path: Path) -> None:
    """The digest payload includes the same rows `ratchet metrics` prints."""
    from ratchet.orchestrator.digest import Digest
    from ratchet.orchestrator.metrics import product_metrics

    settings = make_settings(tmp_path)
    store = open_store(settings)
    repo = seeded_repo(settings)
    edit, diff = commit_edit(repo, {"github-triage/NOTE.md": "x\n"})
    store.save_edit(edit, diff)
    store.save_verdict(make_verdict(edit, decision="PASS", action="merged", created_at=clock.now()))
    digest = await run_digest(store, settings)
    rows = [(m.metric, m.value) for m in digest.metrics]
    assert rows[0] == ("Blessed traces", "0")
    assert ("Decision mix", "PASS 100% (1/1), WARN 0% (0/1), BLOCK 0% (0/1)") in rows
    assert [m for m, _ in rows] == [m for m, _ in product_metrics(store, digest.period_start)]
    legacy = digest.model_dump(mode="json")
    del legacy["metrics"]
    assert Digest.model_validate(legacy).metrics == []  # older digests load
