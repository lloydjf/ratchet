"""report.md rendering is pinned by a golden file; the figure is an accessible SVG; compare
reports deltas. Regenerate the golden with ``UPDATE_GOLDEN=1 uv run pytest -k bench_report``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_fixtures import small_results  # noqa: E402

from ratchet.bench.compare import compare_results, render_compare  # noqa: E402
from ratchet.bench.report import ReportInputs, render  # noqa: E402
from ratchet.bench.svg import Point, cost_vs_recall_svg  # noqa: E402

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "bench" / "report_golden.md"
DIFF = "--- a/expense-report/SKILL.md\n+++ b/expense-report/SKILL.md\n@@ -1 +1 @@\n-old\n+new\n"


def _inputs(simulated: bool = True) -> ReportInputs:
    return ReportInputs(
        results=small_results(simulated=simulated),
        out_dir=Path("bench/results/2026-10-16"),
        date="2026-10-16",
        diffs={"r2-a": DIFF},
        human={("r2-a", "t1"): "regression", ("r2-a", "t2"): "pass"},
        label_counts={"R2": 1, "N0": 2},
        reviewed=(1, 3),
    )


def test_report_matches_the_golden_file() -> None:
    text, _ = render(_inputs())
    if os.environ.get("UPDATE_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(text, encoding="utf-8")
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_simulated_report_carries_the_banner_and_live_report_does_not() -> None:
    sim, _ = render(_inputs(simulated=True))
    live, _ = render(_inputs(simulated=False))
    assert "SIMULATED MODELS — pipeline smoke test; not benchmark results" in sim
    assert "SIMULATED" not in live
    assert "`ratchet-sim/nano`" in live


def test_report_has_every_required_section_and_links_numbers_to_configs() -> None:
    text, _ = render(_inputs())
    for heading in (
        "## Table 2",
        "## Figure 1",
        "## E3",
        "## E5",
        "## E6",
        "## Judge agreement",
        "### R2",
        "### R4",
        "### R5",
        "### D1",
        "### N0",
    ):
        assert heading in text, heading
    assert "[0.75 (3/4)](../../configs/cascade.yaml)" in text
    assert "```diff" in text and "+new" in text
    assert "0.75 pass rate (4 unchanged-skill pairs)" in text  # E6 floor next to the recall table
    assert "Git sha | `abc1234`" in text


def test_cost_vs_recall_svg_is_accessible_and_labels_every_point() -> None:
    svg = cost_vs_recall_svg(
        [Point("cascade", 0.004, 0.8), Point("ultra_only", 0.03, 0.9)], title="T", subtitle="S"
    )
    assert 'role="img"' in svg and '<title id="fig-title">T</title>' in svg
    assert "cascade: $0.0040 per verdict, recall 0.80" in svg
    assert "Detection recall" in svg and "Mean judge cost per verdict" in svg
    assert "prefers-color-scheme: dark" in svg
    assert cost_vs_recall_svg([], title="empty").startswith("<svg")


def test_compare_reports_deltas_per_shared_config() -> None:
    a, b = small_results(), small_results()
    worse = [e.model_copy(update={"decision": "PASS"}) for e in b.runs["cascade"].edits]
    b.runs["cascade"] = b.runs["cascade"].model_copy(update={"edits": worse})
    deltas = {(d.config, d.metric): d for d in compare_results(a, b)}
    assert deltas[("cascade", "recall")].delta == -0.75
    assert ("noise", "recall") not in deltas
    text = render_compare(Path("a"), Path("b"), a, b)
    assert "| `cascade` | recall | 0.7500 | 0.0000 | -0.7500 |" in text
    assert "SIMULATED" in text
