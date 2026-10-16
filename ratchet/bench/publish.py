"""Write ``report.md`` + ``cost_vs_recall.svg`` for a results directory (the I/O around
:func:`ratchet.bench.report.render`)."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from ratchet import clock
from ratchet.bench.labels import LabelSet, edit_diff, load_labels
from ratchet.bench.report import FIGURE_NAME, ReportInputs, render
from ratchet.bench.results import load_results
from ratchet.errors import NotFoundError

__all__ = ["write_report", "human_reference", "report_date"]

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def human_reference(labels: LabelSet) -> dict[tuple[str, str], str]:
    return {
        (lab.edit_id, task_id): value
        for lab in labels.labels
        for task_id, value in lab.review.pair_labels.items()
    }


def report_date(out_dir: Path) -> str:
    match = _DATE.match(out_dir.name)
    return match.group(0) if match else clock.now().date().isoformat()


def write_report(out_dir: Path, *, labels_path: Path, edits_root: Path, skills_root: Path) -> Path:
    results = load_results(out_dir / "results.json")
    if results is None:
        raise NotFoundError(f"no results.json in {out_dir}", code="not_found")
    labels = load_labels(labels_path)
    diffs: dict[str, str] = {}
    for lab in labels.labels:
        try:
            diffs[lab.edit_id] = edit_diff(edits_root, skills_root, lab.edit_id)
        except NotFoundError:
            continue
    required = [lab for lab in labels.labels if lab.review.required]
    done = [lab for lab in required if lab.review.status in ("agreed", "disagreed")]
    inputs = ReportInputs(
        results=results,
        out_dir=out_dir,
        date=report_date(out_dir),
        diffs=diffs,
        human=human_reference(labels),
        label_counts=dict(Counter(lab.type for lab in labels.labels)),
        reviewed=(len(done), len(required)),
    )
    text, svg = render(inputs)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    (out_dir / FIGURE_NAME).write_text(svg, encoding="utf-8")
    return out_dir / "report.md"
