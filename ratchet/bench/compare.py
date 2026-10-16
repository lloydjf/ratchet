"""``ratchet bench compare <a> <b>``: metric deltas between two results directories, per
configuration present in both (b − a). A simulated side is flagged in the header."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ratchet.bench.metrics import Rate, RunMetrics, summarize
from ratchet.bench.results import SIMULATED_LABEL, ResultsFile, load_results
from ratchet.bench.taxonomy import REGRESSION_TYPES
from ratchet.errors import NotFoundError

__all__ = ["Delta", "METRICS", "compare_results", "compare_dirs", "render_compare"]

Getter = Callable[[RunMetrics], float | None]


def _r(get: Callable[[RunMetrics], Rate]) -> Getter:
    return lambda m: get(m).value


def _type_recall(code: str) -> Getter:
    return lambda m: m.recall_by_type[code].value


METRICS: dict[str, Getter] = {
    **{f"{code} recall": _type_recall(code) for code in REGRESSION_TYPES},
    "recall": _r(lambda m: m.recall),
    "block recall": _r(lambda m: m.block_recall),
    "false-block": _r(lambda m: m.false_block),
    "false-warn N0": _r(lambda m: m.false_warn),
    "drift accuracy": _r(lambda m: m.drift_accuracy),
    "pair precision": _r(lambda m: m.pair_precision),
    "pair recall": _r(lambda m: m.pair_recall),
    "cost/verdict mean (USD)": lambda m: m.cost_total.mean,
    "latency mean (s)": lambda m: m.latency.mean,
}


class Delta:
    def __init__(self, config: str, metric: str, a: float | None, b: float | None) -> None:
        self.config, self.metric, self.a, self.b = config, metric, a, b

    @property
    def delta(self) -> float | None:
        return None if self.a is None or self.b is None else self.b - self.a


def compare_results(a: ResultsFile, b: ResultsFile) -> list[Delta]:
    out: list[Delta] = []
    for name in sorted(set(a.runs) & set(b.runs)):
        if a.runs[name].config.mode != "edits":
            continue
        ma, mb = summarize(a.runs[name].edits), summarize(b.runs[name].edits)
        out += [Delta(name, metric, get(ma), get(mb)) for metric, get in METRICS.items()]
    return out


def _fmt(v: float | None, signed: bool = False) -> str:
    if v is None:
        return "n/a"
    return f"{v:+.4f}" if signed else f"{v:.4f}"


def render_compare(a_dir: Path, b_dir: Path, a: ResultsFile, b: ResultsFile) -> str:
    lines = [f"# Bench compare: `{a_dir}` → `{b_dir}`", ""]
    for side, res in (("a", a), ("b", b)):
        if res.meta.simulated:
            lines.append(f"> side {side}: **{SIMULATED_LABEL}**")
    lines += ["", "| config | metric | a | b | b − a |", "| --- | --- | --- | --- | --- |"]
    for d in compare_results(a, b):
        lines.append(
            f"| `{d.config}` | {d.metric} | {_fmt(d.a)} | {_fmt(d.b)} | {_fmt(d.delta, True)} |"
        )
    return "\n".join(lines) + "\n"


def compare_dirs(a_dir: Path, b_dir: Path) -> str:
    loaded = []
    for d in (a_dir, b_dir):
        res = load_results(d / "results.json")
        if res is None:
            raise NotFoundError(f"no results.json in {d}", code="not_found")
        loaded.append(res)
    return render_compare(a_dir, b_dir, loaded[0], loaded[1])
