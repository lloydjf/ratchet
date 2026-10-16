"""``ratchet bench …``: SkillRegress-Bench. ``bench_app`` is registered in the
main CLI (``app.add_typer(bench_app, name="bench")``).

Every command that can spend on live models prints its cost estimate first and needs
``--yes`` (or an interactive yes) before it starts; ``smoke`` always runs on the simulator.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import typer

from ratchet.bench.dataset import DEFAULT_ROOT, BenchPaths, load_tasks, validate_dataset
from ratchet.bench.env import DEFAULT_WORK, BenchEnv
from ratchet.cli import common
from ratchet.cli.common import fail, run
from ratchet.config.settings import configure

__all__ = ["bench_app", "bench_spend_line"]

bench_app = typer.Typer(
    help="SkillRegress-Bench: build, label, run, compare.", no_args_is_help=True
)

_ROOT = typer.Option(DEFAULT_ROOT, "--bench", help="The bench dataset directory.")
_WORK = typer.Option(DEFAULT_WORK, "--work", help="Bench work area (its own store and repo).")
_YES = typer.Option(False, "--yes", "-y", help="Confirm live model spend without a prompt.")
_CONFIG = typer.Option(..., "--config", help="bench/configs/<name>.yaml")
_OUT = typer.Option(..., "--out", help="Results directory, e.g. bench/results/<date>/")
_RESULTS = typer.Argument(..., help="Results directory.")
_A = typer.Argument(..., help="Baseline results directory.")
_B = typer.Argument(..., help="Results directory to compare.")
_COMPARE_OUT = typer.Option(None, "--out", help="Also write the table to this file.")
_PAIR = typer.Option(None, "--pair", help="task_id=regression|pass|drift (repeatable).")
_SMOKE_OUT = typer.Option(Path("bench/results/smoke-sim"), "--out", help="Smoke results dir.")
_EDITS = typer.Option(None, "--edits", help="Edits dir (default: <bench>/edits).")
_SMOKE_WORK = typer.Option(Path("var/bench-smoke"), "--work", help="Smoke work area.")


def _split(value: str | None) -> list[str] | None:
    return [v.strip() for v in value.split(",") if v.strip()] if value else None


def _confirm(yes: bool) -> Callable[[str], bool]:
    def ask(question: str) -> bool:
        if yes:
            return True
        if not sys.stdin.isatty():
            return False
        return typer.confirm(question, default=False)

    return ask


def _env(root: Path, work: Path) -> BenchEnv:
    env = BenchEnv.open(BenchPaths(root), work, common.settings())
    configure(env.settings)
    return env


def _command(name: str) -> str:
    """The command line as typed, normalized to ``ratchet bench <name> …`` (report header)."""
    argv = sys.argv
    tail = argv[argv.index(name) :] if name in argv else [name]
    return "ratchet bench " + " ".join(tail)


def bench_spend_line(env: BenchEnv) -> str:
    """The bench cap is all-time (``proxy.policy``): what the bench store has spent so far."""
    spent = env.store.spend(purpose="bench").total_usd
    cap = env.settings.ratchet_bench_usd_cap
    return f"bench spend so far (all-time): ${spent:.2f} of ${cap:.2f}"


def _banner(env: BenchEnv) -> None:
    if env.settings.simulated:
        typer.echo("SIMULATED MODELS (RATCHET_UPSTREAM=sim): pipeline test only, not results.")
    else:
        typer.echo("LIVE MODELS: this spends real credits under the bench cap.")
    typer.echo(bench_spend_line(env))


@bench_app.command("validate")
def validate(root: Path = _ROOT) -> None:
    """Check the dataset: skills (agentskills.io), world, workspaces, trigger routing."""
    problems = validate_dataset(BenchPaths(root))
    for problem in problems:
        typer.echo(f"  {problem}")
    if problems:
        raise typer.Exit(1)
    typer.echo(f"bench dataset ok: {len(load_tasks(BenchPaths(root).tasks))} tasks")


@bench_app.command("build")
def build(
    root: Path = _ROOT,
    work: Path = _WORK,
    goldens: bool = typer.Option(True, "--goldens/--no-goldens"),
    controls: bool = typer.Option(True, "--controls/--no-controls"),
    inject: bool = typer.Option(True, "--inject/--no-inject"),
    skills: str | None = typer.Option(None, help="Comma-separated skills (default: all)."),
    types: str | None = typer.Option(None, help="Comma-separated R types to inject."),
    force: bool = typer.Option(False, "--force", help="Redo goldens and labeled edits."),
    yes: bool = _YES,
) -> None:
    """Record + bless goldens, generate controls, inject R1–R6 (dataset construction)."""
    from ratchet.bench.cli_support import build_cmd

    env = _env(root, work)
    _banner(env)
    opts = {"goldens": goldens, "controls": controls, "inject": inject, "force": force}
    run(build_cmd(env, _split(skills), _split(types), opts, typer.echo, _confirm(yes)))


@bench_app.command("run")
def run_cmd(
    config: Path = _CONFIG,
    out: Path = _OUT,
    root: Path = _ROOT,
    work: Path = _WORK,
    limit: int | None = typer.Option(None, help="Run at most N edits (seeded, by type)."),
    types: str | None = typer.Option(None, help="Comma-separated types, e.g. R2,R4,N0."),
    seed: int = typer.Option(0, help="Seed for the --limit sample."),
    live_tavily: bool = typer.Option(False, "--live-tavily", help="Re-query Tavily live."),
    concurrency: int | None = typer.Option(None, help="Edits in flight (default: config)."),
    yes: bool = _YES,
) -> None:
    """Run one configuration over the labeled edits; write results.json and report.md."""
    from ratchet.bench.publish import write_report
    from ratchet.bench.runner import RunOptions, run_config

    env = _env(root, work)
    _banner(env)
    opts = RunOptions(
        config_path=config,
        out=out,
        limit=limit,
        types=_split(types),
        seed=seed,
        live_tavily=live_tavily,
        concurrency=concurrency,
        command=_command("run"),
    )
    run(run_config(env, opts, echo=typer.echo, confirm=_confirm(yes)))
    path = write_report(
        out, labels_path=env.labels_path, edits_root=env.edits_root, skills_root=env.paths.skills
    )
    typer.echo(f"report: {path}")


@bench_app.command("report")
def report(out: Path = _RESULTS, root: Path = _ROOT, edits: Path | None = _EDITS) -> None:
    """Re-render report.md and cost_vs_recall.svg from results.json (no model calls)."""
    from ratchet.bench.publish import write_report

    paths = BenchPaths(root)
    labels = out / "labels.json" if (out / "labels.json").is_file() else paths.labels
    path = write_report(
        out, labels_path=labels, edits_root=edits or paths.edits, skills_root=paths.skills
    )
    typer.echo(str(path))


@bench_app.command("compare")
def compare(
    a: Path = _A,
    b: Path = _B,
    out: Path | None = _COMPARE_OUT,
) -> None:
    """Metric deltas (b − a) per configuration present in both."""
    from ratchet.bench.compare import compare_dirs
    from ratchet.errors import RatchetError

    try:
        text = compare_dirs(a, b)
    except RatchetError as exc:
        raise fail(f"error {exc.code}: {exc.message}") from exc
    typer.echo(text)
    if out is not None:
        out.write_text(text, encoding="utf-8")


@bench_app.command("label")
def label(
    edit_id: str | None = typer.Argument(None, help="Edit to review (default: next pending)."),
    root: Path = _ROOT,
    agree: bool | None = typer.Option(None, "--agree/--disagree", help="Your verdict."),
    decision: str | None = typer.Option(None, help="Corrected decision on --disagree."),
    pair: list[str] | None = _PAIR,
    reviewer: str = typer.Option("reviewer", help="Your name or handle."),
    note: str = typer.Option("", help="Free-text note."),
    status: bool = typer.Option(False, "--status", help="Only list the review queue."),
) -> None:
    """Show an edit's diff and labels and record a human verdict (labeling step 4)."""
    from ratchet.bench.cli_support import label_cmd

    label_cmd(
        BenchPaths(root),
        edit_id,
        agree=agree,
        decision=decision,
        pairs=pair or [],
        reviewer=reviewer,
        note=note,
        status_only=status,
        echo=typer.echo,
        interactive=sys.stdin.isatty(),
    )


@bench_app.command("smoke")
def smoke(
    out: Path = _SMOKE_OUT,
    work: Path = _SMOKE_WORK,
    root: Path = _ROOT,
    configs: str | None = typer.Option(None, help="Comma-separated configs (default: all)."),
) -> None:
    """Bench smoke test on the offline simulator: build, run every config, write the report."""
    from ratchet.bench.smoke import CONFIG_ORDER, SmokeOptions, run_smoke

    opts = SmokeOptions(work=work, out=out, configs=_split(configs) or list(CONFIG_ORDER))
    typer.echo("SIMULATED MODELS — pipeline smoke test; not benchmark results.")
    run(run_smoke(BenchPaths(root), common.settings(), opts, echo=typer.echo))
    typer.echo(f"smoke report: {out / 'report.md'}")


if __name__ == "__main__":  # `python -m ratchet.cli.bench …` until the main CLI registers it
    bench_app()
