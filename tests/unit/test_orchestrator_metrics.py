"""Product metrics and the demo funnel."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import T0, commit_edit, make_settings, make_verdict, open_store, pair, seeded_repo  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.orchestrator.metrics import demo_funnel, product_metrics  # noqa: E402


def test_product_metrics_summarize_verdicts_and_blesses(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    repo = seeded_repo(settings)
    with clock.frozen(T0):
        store.emit("trace.blessed", "run_a", {})
    for n, decision in enumerate(("PASS", "BLOCK")):
        edit, diff = commit_edit(repo, {f"github-triage/N{n}.md": "x\n"})
        store.save_edit(edit, diff)
        pairs = [pair(outcome="pass"), pair("run_g2", "run_r2", "uncertain")]
        store.save_verdict(make_verdict(edit, decision=decision, created_at=T0, pairs=pairs))
    rows = dict(product_metrics(store, T0.replace(hour=0)))
    assert rows["Blessed traces"] == "1" and rows["Protected edits"] == "2"
    assert rows["Decision mix"].startswith("PASS 50% (1/2)")
    assert rows["Uncertain rate"] == "50% (2/4)"
    assert rows["Block precision proxy"] == "100% (1/1)"


def test_demo_funnel_counts_sessions_per_step(tmp_path: Path) -> None:
    store = open_store(make_settings(tmp_path))
    for session in ("a", "b"):
        store.emit("demo.landing_view", "-", {}, session_hash=session, table="demo_events")
    store.emit("demo.simulate_click", "-", {}, session_hash="a", table="demo_events")
    rows = dict(demo_funnel(store, T0.replace(year=2000)))
    assert rows["demo.landing_view"] == "2"
    assert rows["demo.simulate_click"].startswith("1 (50% (1/2)")
