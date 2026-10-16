"""``report.md`` and ``cost_vs_recall.svg``.

1. Header: harness, models (or the SIMULATED banner), date, git sha, labels, commands.
2. Table 2: recall per type (R1–R6), block recall, false blocks on N0 and D1, drift accuracy,
   cost per verdict, for the four E4 configurations — the E6 noise floor in every row — and
   the prompt-cache share (prompt-cache hits over prompt tokens, all runs, per tier).
3. Figure 1: cost per verdict vs detection recall, one point per configuration.
4. E3 (drift on/off), E5 (Level 0 on/off), E6 (noise floor), pair-level P/R, latency, κ.
5. Five worked examples (R2, R4, R5, D1, N0): diff, pair evidence, narrative.

Every number links to the configuration file that produced it; the section captions name
the functions in ``ratchet/bench/metrics.py`` that computed it.
"""

from __future__ import annotations

import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ratchet.bench.examples import worked_examples
from ratchet.bench.metrics import Kappa, Rate, RunMetrics, Stats, summarize
from ratchet.bench.results import SIMULATED_LABEL, ResultsFile, RunResult
from ratchet.bench.svg import Point, cost_vs_recall_svg
from ratchet.bench.taxonomy import NAMES, REGRESSION_TYPES
from ratchet.models import TIERS

__all__ = ["ReportInputs", "render", "fmt_rate", "fmt_usd", "E4_ORDER", "FIGURE_NAME"]

E4_ORDER = ("nano_only", "super_only", "ultra_only", "cascade")
FIGURE_NAME = "cost_vs_recall.svg"
_METRICS = "../../../ratchet/bench/metrics.py"


@dataclass(frozen=True)
class ReportInputs:
    results: ResultsFile
    out_dir: Path
    date: str
    diffs: Mapping[str, str] = field(default_factory=dict)
    human: Mapping[tuple[str, str], str] = field(default_factory=dict)
    label_counts: Mapping[str, int] = field(default_factory=dict)
    reviewed: tuple[int, int] = (0, 0)  # (reviews done, reviews required)


def fmt_rate(r: Rate) -> str:
    return "n/a (0)" if r.value is None else f"{r.value:.2f} ({r.num}/{r.den})"


def fmt_usd(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:.5f}" if value < 0.01 else f"${value:.4f}"


def _stats(s: Stats, money: bool = True) -> str:
    if s.mean is None:
        return "n/a"
    if money:
        return f"{fmt_usd(s.mean)} / {fmt_usd(s.p95)}"
    return f"{s.mean:.1f} s / {(s.p95 or 0.0):.1f} s"


def _kappa(k: Kappa) -> str:
    return f"n/a (n={k.n})" if k.value is None else f"{k.value:.2f} (n={k.n})"


class _Ctx:
    def __init__(self, inputs: ReportInputs) -> None:
        self.inputs = inputs
        runs = inputs.results.runs
        noise_runs = [r for r in runs.values() if r.config.mode == "noise"]
        self.noise = summarize([], noise=noise_runs[0].edits).noise if noise_runs else None
        self.noise_run = noise_runs[0] if noise_runs else None
        self.metrics: dict[str, RunMetrics] = {
            name: summarize(
                run.edits,
                human=inputs.human,
                noise=self.noise_run.edits if self.noise_run else None,
            )
            for name, run in runs.items()
            if run.config.mode == "edits"
        }

    def link(self, run: RunResult | None, text: str) -> str:
        if run is None or not run.config_path:
            return text
        rel = os.path.relpath(Path(run.config_path).absolute(), self.inputs.out_dir.absolute())
        return f"[{text}]({Path(rel).as_posix()})"

    def run(self, name: str) -> RunResult | None:
        return self.inputs.results.runs.get(name)

    @property
    def noise_text(self) -> str:
        if self.noise is None or self.noise.pass_rate is None:
            return "not measured"
        text = f"{self.noise.pass_rate:.2f} pass rate ({self.noise.n_pairs} unchanged-skill pairs)"
        controls = self.noise_run is not None and self.noise_run.config.cascade.controls
        if not controls:
            return text
        text += " with control replays"
        if self.noise.noisy_rate is not None:
            text += f"; {self.noise.noisy_rate:.2f} of pairs diverged on the control replay"
        return text


def _header(ctx: _Ctx) -> list[str]:
    meta = ctx.inputs.results.meta
    lines = [f"# SkillRegress-Bench results — {ctx.inputs.date}", ""]
    if meta.simulated:
        lines += [
            f"> **{SIMULATED_LABEL}.** Every number below comes from the offline",
            (
                "> simulator (ADR 0018), not from Nemotron models; it shows that the pipeline and "
                "the report work, and must never be quoted as a result."
            ),
            "",
        ]
    models = (
        "SIMULATED — smoke test, not results"
        if meta.simulated
        else ", ".join(f"{t}: `{m}`" for t, m in meta.models.items())
    )
    counts = ", ".join(f"{t} {n}" for t, n in sorted(ctx.inputs.label_counts.items()))
    done, required = ctx.inputs.reviewed
    lines += [
        "| | |",
        "| --- | --- |",
        f"| Harness | `{meta.harness}` (agent tier `{meta.agent_tier}`) |",
        f"| Models | {models} |",
        f"| Date | {ctx.inputs.date} (results updated {meta.updated_at:%Y-%m-%d %H:%M} UTC) |",
        f"| Git sha | `{meta.git_sha}` |",
        f"| Labels | `{meta.labels_path}` — {meta.n_labels} edits ({counts}) |",
        f"| Human review | {done} of {required} required reviews recorded |",
        f"| E6 noise floor | {ctx.noise_text} |",
        "",
        "Produced by:",
        "",
    ]
    for run in ctx.inputs.results.runs.values():
        cmd = run.command or f"ratchet bench run --config {run.config_path}"
        lines.append(f"- `{run.config.name}` ({', '.join(run.config.experiments) or '—'}): `{cmd}`")
    return [*lines, ""]


def _main_table(ctx: _Ctx) -> list[str]:
    names = [n for n in E4_ORDER if n in ctx.metrics]
    head = [
        *(f"{t} recall" for t in REGRESSION_TYPES),
        "recall",
        "block recall",
        "false-block N0",
        "false-block D1",
        "drift acc.",
        "cost/verdict mean / p95",
        "all-Ultra mean",
        "E6 noise floor",
    ]
    lines = [
        "## Table 2 — detection, false blocks and cost per configuration (E1, E2, E4)",
        "",
        f"Computed by `summarize` in [`metrics.py`]({_METRICS}). Recall counts BLOCK or WARN; "
        "read every recall figure against the E6 noise floor in the last column.",
        "",
        "| config | " + " | ".join(head) + " |",
        "| --- " * (len(head) + 1) + "|",
    ]
    for name in names:
        m, run = ctx.metrics[name], ctx.run(name)
        cells = [fmt_rate(m.recall_by_type[t]) for t in REGRESSION_TYPES]
        cells += [
            fmt_rate(m.recall),
            fmt_rate(m.block_recall),
            fmt_rate(m.false_block_by_type["N0"]),
            fmt_rate(m.false_block_by_type["D1"]),
            fmt_rate(m.drift_accuracy),
            _stats(m.cost_total),
            fmt_usd(m.cost_counterfactual.mean),
            ctx.noise_text,
        ]
        lines.append(f"| `{name}` | " + " | ".join(ctx.link(run, c) for c in cells) + " |")
    if not names:
        lines.append("| — | no E4 configuration has been run yet |")
    return [*lines, ""]


def _share(cached: int, prompt: int) -> str:
    return "n/a" if prompt <= 0 else f"{cached / prompt:.2f}"


def _cache_share(ctx: _Ctx) -> list[str]:
    """Prompt-cache hits over prompt tokens, from every edit's proxy ledger rows."""
    prompt: Counter[str] = Counter()
    cached: Counter[str] = Counter()
    for run in ctx.inputs.results.runs.values():
        for edit in run.edits:
            prompt.update(edit.prompt_tokens_by_tier)
            cached.update(edit.cached_prompt_tokens_by_tier)
    total = sum(prompt.values())
    if total <= 0:
        return ["Prompt-cache share: not recorded (no prompt tokens in these results).", ""]
    per_tier = ", ".join(f"{t} {_share(cached[t], prompt[t])}" for t in TIERS)
    return [
        f"Prompt-cache share: {_share(sum(cached.values()), total)} of prompt tokens were "
        f"cache hits (per tier: {per_tier}).",
        "",
    ]


def _figure(ctx: _Ctx) -> tuple[list[str], str]:
    points = [
        Point(name, m.cost_total.mean or 0.0, m.recall.value or 0.0)
        for name in E4_ORDER
        if (m := ctx.metrics.get(name)) is not None and m.recall.value is not None
    ]
    sub = (
        SIMULATED_LABEL
        if ctx.inputs.results.meta.simulated
        else f"E6 noise floor: {ctx.noise_text}"
    )
    svg = cost_vs_recall_svg(points, title="Cost per verdict vs detection recall", subtitle=sub)
    alt = "; ".join(f"{p.label} {fmt_usd(p.cost_usd)} → recall {p.recall:.2f}" for p in points)
    lines = [
        "## Figure 1 — cost per verdict vs detection recall (E4)",
        "",
        f"![Cost per verdict vs detection recall: {alt or 'no data'}]({FIGURE_NAME})",
        "",
    ]
    return lines, svg


def _pair_table(ctx: _Ctx, title: str, names: Sequence[str], cols: Sequence[str]) -> list[str]:
    present = [n for n in names if n in ctx.metrics]
    lines = [title, "", "| config | " + " | ".join(cols) + " |", "| --- " * (len(cols) + 1) + "|"]
    for name in present:
        m, run = ctx.metrics[name], ctx.run(name)
        values = {
            "recall": fmt_rate(m.recall),
            "R3 recall": fmt_rate(m.recall_by_type["R3"]),
            "R4 recall": fmt_rate(m.recall_by_type["R4"]),
            "false-block D1": fmt_rate(m.false_block_by_type["D1"]),
            "false-block": fmt_rate(m.false_block),
            "false-warn N0": fmt_rate(m.false_warn),
            "drift acc.": fmt_rate(m.drift_accuracy),
            "cost/verdict mean / p95": _stats(m.cost_total),
            "pair precision": fmt_rate(m.pair_precision),
            "pair recall": fmt_rate(m.pair_recall),
            "latency mean / p95": _stats(m.latency, money=False),
            "errors": f"{m.n_errors}/{m.n_edits}",
        }
        lines.append(f"| `{name}` | " + " | ".join(ctx.link(run, values[c]) for c in cols) + " |")
    if not present:
        lines.append("| — | not run |")
    return [*lines, ""]


def _noise(ctx: _Ctx) -> list[str]:
    lines = ["## E6 — noise floor (unchanged skill, replayed)", ""]
    if ctx.noise is None or ctx.noise_run is None:
        return [*lines, "Not run yet (`bench/configs/noise.yaml`).", ""]
    counts = ", ".join(f"{k} {v}" for k, v in sorted(ctx.noise.by_outcome.items()))
    reps = Counter(e.repeat for e in ctx.noise_run.edits)
    lines += [
        f"{ctx.link(ctx.noise_run, ctx.noise_text)}; outcomes: {counts}; "
        f"{len(ctx.noise_run.edits)} edits over {len(reps)} repeat(s) (`noise_floor` in "
        f"[`metrics.py`]({_METRICS})). A pass rate below 1.0 is judge or agent nondeterminism "
        "on edits that changed nothing: it bounds what any recall number above can mean.",
        "",
    ]
    return lines


def _kappas(ctx: _Ctx) -> list[str]:
    lines = [
        "## Judge agreement — Cohen's κ per tier",
        "",
        "Binary per pair (`regressed`/`uncertain` = flag), `kappa_by_tier` in "
        f"[`metrics.py`]({_METRICS}). Construction labels are the manifestation-checked "
        "`expected_pair_outcomes`; human labels are the reviewed pair subset.",
        "",
        "| config | "
        + " | ".join(f"{t} vs construction" for t in TIERS)
        + " | "
        + " | ".join(f"{t} vs human" for t in TIERS)
        + " |",
        "| --- " * (2 * len(TIERS) + 1) + "|",
    ]
    for name, m in ctx.metrics.items():
        cells = [_kappa(m.kappa_construction[t]) for t in TIERS]
        cells += [_kappa(m.kappa_human[t]) for t in TIERS]
        lines.append(
            f"| `{name}` | " + " | ".join(ctx.link(ctx.run(name), c) for c in cells) + " |"
        )
    return [*lines, ""]


def render(inputs: ReportInputs) -> tuple[str, str]:
    """``(report.md, cost_vs_recall.svg)`` — pure, so the golden-file test can pin it."""
    ctx = _Ctx(inputs)
    figure, svg = _figure(ctx)
    lines = [*_header(ctx), *_main_table(ctx), *_cache_share(ctx), *figure]
    lines += _pair_table(
        ctx,
        "## E3 — drift disambiguation on vs off",
        ["cascade", "drift_off"],
        ["drift acc.", "false-block D1", "false-block", "recall"],
    )
    lines += _pair_table(
        ctx,
        "## E5 — Level 0 on vs off",
        ["cascade", "level0_off"],
        ["R3 recall", "R4 recall", "recall", "cost/verdict mean / p95"],
    )
    lines += _noise(ctx)
    lines += _pair_table(
        ctx,
        "## Pair level, false warns and latency",
        list(ctx.metrics),
        ["pair precision", "pair recall", "false-warn N0", "latency mean / p95", "errors"],
    )
    reused = [n for n, r in inputs.results.runs.items() if r.config.reuse_replays]
    if reused:
        names = ", ".join(f"`{n}`" for n in reused)
        lines += [f"Latency for {names} covers judging only: those runs re-judge the "
                  "`cascade` run's replays.", ""]  # fmt: skip
    lines += _kappas(ctx)
    primary = ctx.run("cascade") or next(iter(inputs.results.runs.values()), None)
    lines += worked_examples(primary, inputs.diffs, ctx.link)
    lines += ["## Taxonomy", "", "| code | name |", "| --- | --- |"]
    lines += [f"| {code} | {name} |" for code, name in NAMES.items()]
    return "\n".join(lines).rstrip() + "\n", svg
