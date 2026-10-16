"""The five worked examples of the report: one each for R2, R4,
R5, D1 and N0, taken deterministically (the first edit of that type by id, whatever its
outcome — a miss is shown as a miss) with the diff, the pair evidence and the narrative."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ratchet.bench.results import EditResult, PairResult, RunResult
from ratchet.bench.taxonomy import NAMES

__all__ = ["EXAMPLE_TYPES", "pick_examples", "worked_examples", "pair_row"]

EXAMPLE_TYPES = ("R2", "R4", "R5", "D1", "N0")
_MAX_DIFF_LINES = 60
_MAX_PAIRS = 12
Link = Callable[[RunResult | None, str], str]


def pick_examples(run: RunResult) -> dict[str, EditResult]:
    chosen: dict[str, EditResult] = {}
    for edit in sorted(run.edits, key=lambda e: e.bench_id):
        if edit.type in EXAMPLE_TYPES and edit.type not in chosen and edit.error is None:
            chosen[edit.type] = edit
    return chosen


def _opinions(p: PairResult) -> str:
    if not p.opinions:
        return "—"
    return " → ".join(f"{o.tier} {o.verdict} {o.confidence:.2f}" for o in p.opinions)


def _level0(p: PairResult) -> str:
    parts = []
    if p.level0_hard_fail:
        parts.append("hard fail")
    for n, what in (
        (p.level0_new, "new"),
        (p.level0_missing, "missing"),
        (p.level0_changed, "changed"),
    ):
        if n:
            parts.append(f"{n} {what}")
    return ", ".join(parts) or "—"


def _drift(p: PairResult) -> str:
    if not p.drift:
        return "—"
    return "; ".join(
        f"{d.fact_id}: {'changed' if d.changed else 'unchanged'}"
        + (f" → {d.new_value}" if d.changed and d.new_value else "")
        + ("" if d.status == "checked" else f" ({d.status})")
        for d in p.drift
    )


def pair_row(p: PairResult) -> str:
    cells = [
        p.task_id,
        p.outcome,
        p.expected_outcome,
        p.decided_by or "—",
        _opinions(p),
        _level0(p),
        _drift(p),
    ]
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def _diff(text: str) -> list[str]:
    lines = text.splitlines()
    if len(lines) > _MAX_DIFF_LINES:
        lines = [*lines[:_MAX_DIFF_LINES], f"… ({len(lines) - _MAX_DIFF_LINES} more lines)"]
    return ["```diff", *lines, "```"] if lines else ["(no diff recorded)"]


def _example(edit: EditResult, diff: str, link: Callable[[str], str]) -> list[str]:
    verdict = "correct" if edit.decision == edit.expected_decision else "incorrect"
    if edit.type not in ("N0", "D1") and edit.decision == "WARN":
        verdict = "detected (WARN, not blocked)"
    lines = [
        f"### {edit.type} — {NAMES.get(edit.type, edit.type)}: `{edit.bench_id}`",
        "",
        f"Skill `{edit.skill}`. Expected **{edit.expected_decision}**, got "
        f"**{link(str(edit.decision))}** ({verdict}); reasons `{', '.join(edit.reasons) or '—'}`; "
        f"judge cost {link(f'${edit.judge_usd:.5f}')}.",
        "",
        *_diff(diff),
        "",
        "| golden task | outcome | expected | decided by | opinions | Level 0 | drift |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    pairs = sorted(edit.pairs, key=lambda p: (p.expected_outcome == "pass", p.task_id))
    lines += [pair_row(p) for p in pairs[:_MAX_PAIRS]]
    if len(pairs) > _MAX_PAIRS:
        lines.append(f"| … {len(pairs) - _MAX_PAIRS} more pairs | | | | | | |")
    narrative = " ".join(edit.narrative.split())
    lines += ["", f"> {narrative}" if narrative else "> (no narrative)", ""]
    return lines


def worked_examples(run: RunResult | None, diffs: Mapping[str, str], link: Link) -> list[str]:
    lines = ["## Worked examples", ""]
    if run is None:
        return [*lines, "No run yet.", ""]
    chosen = pick_examples(run)
    lines += [f"From the `{run.config.name}` run.", ""]
    for code in EXAMPLE_TYPES:
        edit = chosen.get(code)
        if edit is None:
            lines += [f"### {code} — {NAMES[code]}", "", "No edit of this type in the run.", ""]
            continue
        lines += _example(edit, diffs.get(edit.bench_id, ""), lambda text: link(run, text))
    return lines
