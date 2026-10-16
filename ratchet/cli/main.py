"""The ``ratchet`` CLI (design §9). Every command calls the same
orchestrator functions the dashboard API calls; there is no CLI-only path for the loop."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time

import typer

from ratchet import clock
from ratchet.cli import common, report
from ratchet.cli.bench import bench_app
from ratchet.cli.common import fail, run
from ratchet.config.settings import Settings
from ratchet.errors import RatchetError

__all__ = ["app", "main"]

app = typer.Typer(help="Ratchet: regression CI for self-improving agents.", no_args_is_help=True)
agent_app = typer.Typer(help="Run the agent on the control plane.", no_args_is_help=True)
digest_app = typer.Typer(help="Weekly digest.", no_args_is_help=True)
db_app = typer.Typer(help="Database.", no_args_is_help=True)
schemas_app = typer.Typer(help="JSON schemas.", no_args_is_help=True)
api_app = typer.Typer(help="Dashboard API.", no_args_is_help=True)
for sub, name in (
    (agent_app, "agent"),
    (digest_app, "digest"),
    (db_app, "db"),
    (schemas_app, "schemas"),
    (api_app, "api"),
    (bench_app, "bench"),
):
    app.add_typer(sub, name=name)


def _print_version(value: bool) -> None:
    if value:
        from ratchet import __version__

        typer.echo(f"ratchet {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    ctx: typer.Context,
    _version: bool = typer.Option(
        False, "--version", callback=_print_version, is_eager=True, help="Print the version."
    ),
) -> None:
    """JSON log lines go to stderr so stdout carries only the command's own output;
    ``serve`` is a long-running service and keeps logging to stdout (journald)."""
    from ratchet.log import set_log_stream

    set_log_stream("stdout" if ctx.invoked_subcommand == "serve" else "stderr")


# --- operations -------------------------------------------------------------------------------
@app.command()
def doctor() -> None:
    """Check settings, keys, model ids, runner, sandbox, Tavily, git, disk and caps."""
    from ratchet.cli.doctor import run_checks

    cfg = common.settings()
    checks = run_checks(cfg, common.store(cfg))
    typer.echo(
        common.table(["check", "status", "detail"], [(c.name, c.status, c.detail) for c in checks])
    )
    if any(c.status == "fail" for c in checks):
        raise typer.Exit(1)


@app.command()
def serve(
    no_watch: bool = typer.Option(False, "--no-watch", help="Do not watch the skills repo."),
) -> None:
    """Dashboard (8080), proxy (8081), mock tools (8082) and the orchestrator in one process."""
    from ratchet.serve import serve as serve_all

    run(serve_all(common.settings(), watch=not no_watch))


@app.command("seed-demo")
def seed_demo(force: bool = typer.Option(False, "--force", help="Re-record every golden.")) -> None:
    """Initialize the demo skills repo and record + bless the 25 demo goldens."""
    run(_seed(common.settings(), force=force))


async def _seed(cfg: Settings, *, force: bool) -> None:
    from ratchet.orchestrator import demo

    tasks = demo.load_tasks(cfg.ratchet_scenarios_dir / "golden" / "tasks.yaml")
    if not cfg.simulated:
        typer.echo(f"Recording on live models costs about ${demo.estimate_usd(len(tasks)):.2f}.")
    store = await asyncio.to_thread(common.store, cfg)
    result = await demo.seed_demo(
        cfg, store, force=force, on_task=lambda t, r: typer.echo(f"  blessed {r}  {t.label}")
    )
    typer.echo(
        f"Demo seeded: {result.recorded} recorded, {result.skipped} already blessed, "
        f"{len(result.canaries)} canaries, ${result.usd:.4f}."
    )
    if result.recorded_runs:
        typer.echo(f"Recorded runs in the store: scenarios {', '.join(result.recorded_runs)}.")


@app.command("verify-live")
def verify_live(yes: bool = typer.Option(False, "--yes", help="Skip the cost prompt.")) -> None:
    """Live model and Tavily checks with real keys (about $0.05); appends to docs/VERIFIED.md."""
    from pathlib import Path

    from ratchet.cli import verify_live as live

    cfg = common.settings()
    missing = live.missing_keys(cfg)
    if "NEBIUS_TOKEN_FACTORY_KEY" in missing:
        raise fail("verify-live needs NEBIUS_TOKEN_FACTORY_KEY; nothing was called.")
    typer.echo(f"This makes real calls costing about ${live.ESTIMATE_USD:.2f}.")
    if not yes and not typer.confirm("Continue?"):
        raise typer.Exit(1)
    store = common.store(cfg)
    before = store.spend().total_usd
    results = run(live.run_checks(cfg, store))
    spent = store.spend().total_usd - before
    typer.echo(
        common.table(["check", "ok", "detail"], [(r.check, r.ok, r.detail) for r in results])
    )
    live.append_results(Path("docs/VERIFIED.md"), results, spent)
    if not all(r.ok for r in results):
        raise typer.Exit(1)


# --- agent ------------------------------------------------------------------------------------
@agent_app.command("run")
def agent_run(task: str = typer.Argument(..., help="The task for the agent.")) -> None:
    """Run one task live through the proxy and mock tools (record mode)."""
    run(_agent(common.settings(), task, label=common.short(task, 60)))


@agent_app.command("start")
def agent_start(
    demo: bool = typer.Option(False, "--demo", help="Run the demo agent's next task."),
) -> None:
    """Run the seeded demo agent's next task."""
    from ratchet.orchestrator.demo import next_demo_task

    if not demo:
        raise fail("only `ratchet agent start --demo` is supported; use `agent run` otherwise")
    cfg = common.settings()
    task = next_demo_task(cfg, common.store(cfg))
    run(_agent(cfg, task.task_input, label=task.label))


async def _agent(cfg: Settings, task: str, *, label: str) -> None:
    from ratchet.mocktools.world import World
    from ratchet.orchestrator.agent import run_task
    from ratchet.orchestrator.demo import task_seed

    store = await asyncio.to_thread(common.store, cfg)
    world = await asyncio.to_thread(World.load, cfg.ratchet_scenarios_dir / "world")
    trace = await run_task(
        task,
        settings=cfg,
        store=store,
        repo_dir=cfg.ratchet_skills_repo,
        world=world,
        frozen_time=None,
        seed=task_seed(task),
        task_label=label,
    )
    typer.echo(f"{trace.run_id} ({'ok' if trace.harness_ok else 'failed'})")
    typer.echo(trace.final_output)


@app.command()
def bless(
    run_id: str = typer.Argument(None, help="A live run id."),
    last: bool = typer.Option(
        False, "--last", help="Bless the most recent live or proxy-captured run."
    ),
    all_demo: bool = typer.Option(False, "--all-demo", help="Record and bless every demo task."),
) -> None:
    """Make a live run golden (PII pass, world facts, side effects, criteria). A run an
    external harness left through the proxy (steps but no trace.json) is finalized first."""
    from ratchet.orchestrator.bless import bless as bless_run

    cfg = common.settings()
    if all_demo:
        run(_seed(cfg, force=False))
        return
    store = common.store(cfg)
    if last:
        from ratchet.orchestrator.bless import last_unblessed

        newest = last_unblessed(store)
        if newest is None:
            raise fail("no live or proxy-captured runs to bless")
        run_id = newest
    if not run_id:
        raise fail("give a run id, --last or --all-demo")
    golden = run(bless_run(store, cfg, run_id))
    typer.echo(
        f"Blessed {golden.run_id}: {len(golden.world_facts)} facts, skills {golden.skills_invoked}"
    )


# --- status and reports -----------------------------------------------------------------------
@app.command()
def status(
    timing: str = typer.Option(None, "--timing", help="Stage timeline of a verdict."),
) -> None:
    """Edits in flight, the last verdicts, today's spend and its prompt-cache share."""
    store = common.store()
    try:
        typer.echo(report.timing_text(store, timing) if timing else report.status_text(store))
    except RatchetError as exc:
        raise fail(f"error {exc.code}: {exc.message}") from exc


@app.command()
def logs(edit: str = typer.Option(..., "--edit", help="The edit id.")) -> None:
    """Events and worker logs for one edit."""
    try:
        typer.echo(report.logs_text(common.store(), edit))
    except RatchetError as exc:
        raise fail(f"error {exc.code}: {exc.message}") from exc


@app.command()
def metrics(
    since: str = typer.Option("7d", "--since", help="Window, e.g. 7d or 24h."),
    demo: bool = typer.Option(False, "--demo", help="The anonymous demo funnel instead."),
) -> None:
    """Product metrics (or the demo funnel)."""
    start = clock.now() - common.parse_since(since)
    typer.echo(report.metrics_text(common.store(), start, demo=demo))


# --- the loop ---------------------------------------------------------------------------------
@app.command()
def replay(
    edit_id: str,
    runner: str = typer.Option(None, "--runner", help="local or nebius for this replay."),
) -> None:
    """Re-run an edit from selection (a fresh attempt) and wait for its verdict."""
    if runner not in (None, "local", "nebius"):
        raise typer.BadParameter("runner must be local or nebius")
    run(_replay(common.settings(), edit_id, runner))


async def _replay(cfg: Settings, edit_id: str, runner: str | None) -> None:
    from ratchet.orchestrator.ids import verdict_id_for
    from ratchet.orchestrator.service import Orchestrator

    store = await asyncio.to_thread(common.store, cfg)
    orchestrator = Orchestrator(cfg, store, digest_enabled=False)
    await orchestrator.start(watch=False)
    try:
        await orchestrator.replay(edit_id, runner=runner)
        await orchestrator.wait_idle(cfg.ratchet_replay_timeout_s + 300)
    finally:
        await orchestrator.stop()
    verdict = store.load_verdict(verdict_id_for(edit_id))
    typer.echo(f"{verdict.decision} ({verdict.action}): {verdict.headline}")


@app.command()
def rollback(verdict_id: str) -> None:
    """Revert the edit's commit on candidate; main is left unchanged."""
    from ratchet.orchestrator.actions import rollback as do_rollback

    cfg = common.settings()
    verdict = run(do_rollback(common.store(cfg), cfg, verdict_id, actor="cli"))
    typer.echo(f"{verdict.verdict_id}: {verdict.action} ({verdict.action_commit or '-'})")


@app.command()
def approve(verdict_id: str) -> None:
    """Merge a held edit into main."""
    from ratchet.orchestrator.actions import approve as do_approve

    cfg = common.settings()
    verdict = run(do_approve(common.store(cfg), cfg, verdict_id, actor="cli"))
    typer.echo(f"{verdict.verdict_id}: {verdict.action} ({verdict.action_commit or '-'})")


@app.command()
def rebless(golden: str, replay_run: str = typer.Argument(..., metavar="REPLAY")) -> None:
    """Make the replay the new golden; archive the old one (reason: drift)."""
    from ratchet.orchestrator.bless import rebless as do_rebless

    cfg = common.settings()
    new, archived = run(do_rebless(common.store(cfg), cfg, golden, replay_run))
    typer.echo(f"new golden {new}; archived {archived}")


@app.command()
def simulate(
    scenario: str,
    headless: bool = typer.Option(False, "--headless", help="Print stages and the headline."),
    record_fixture: bool = typer.Option(False, "--record-fixture", help="Save for replayed mode."),
    repeat: int = typer.Option(
        1, "--repeat", min=1, max=50, help="Run N times (fresh session each); report k/N."
    ),
) -> None:
    """Run a seeded scenario; exit 0 iff the decision matches the expected one (every run
    with --repeat). Works whatever RATCHET_SIMULATE_ENABLED says (it gates the HTTP route)."""
    from ratchet.cli.simulate_cmd import (
        print_outcome,
        print_repeat,
        simulate_headless,
        simulate_repeat,
    )

    cfg = common.settings()
    if repeat > 1:
        if record_fixture:
            raise fail("--record-fixture records one run; drop --repeat")
        result = run(simulate_repeat(cfg, common.store(cfg), scenario, repeat=repeat))
        print_repeat(result)
        if not result.all_matched:
            raise typer.Exit(1)
        return
    outcome = run(
        simulate_headless(cfg, common.store(cfg), scenario, record=record_fixture, echo=headless)
    )
    print_outcome(outcome, cfg)
    if not outcome.matched:
        raise typer.Exit(1)


@app.command()
def purge(
    before: str = typer.Option(..., "--before", help="YYYY-MM-DD (UTC)."),
    include_golden: bool = typer.Option(False, "--include-golden"),
) -> None:
    """Delete live and replay traces older than the date (goldens only when asked), then
    cassettes recorded before it that no remaining trace references."""
    try:
        cutoff = datetime.combine(date.fromisoformat(before), time(), tzinfo=clock.now().tzinfo)
    except ValueError as exc:
        raise typer.BadParameter("use YYYY-MM-DD") from exc
    store = common.store()
    n = store.purge(cutoff, include_golden=include_golden)
    m = store.purge_cassettes(cutoff)
    typer.echo(f"Purged {n} traces and {m} unreferenced cassettes.")


@digest_app.command("run")
def digest_run() -> None:
    """Write this week's digest now."""
    from ratchet.orchestrator.digest import run_digest

    cfg = common.settings()
    digest = run(run_digest(common.store(cfg), cfg))
    typer.echo(f"{digest.digest_id}: {digest.narrative}")


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply database migrations (forward-only, idempotent)."""
    from ratchet.store import db

    store = common.store()
    typer.echo(f"Schema version {db.migrate(store.db_path)}.")


@schemas_app.command("export")
def schemas_export(out: str = typer.Option("schemas", "--out")) -> None:
    """Write the data-contract model schemas to schemas/*.json."""
    from pathlib import Path

    from ratchet.cli.schemas import export_schemas

    typer.echo(f"Wrote {len(export_schemas(Path(out)))} schemas to {out}/.")


@api_app.command("export-openapi")
def api_export_openapi() -> None:
    """Print the dashboard API's OpenAPI document (> schemas/openapi.json)."""
    try:
        from ratchet.dashboard_api.openapi import render_openapi
    except ImportError as exc:
        raise fail("the dashboard API is not installed") from exc
    typer.echo(render_openapi(), nl=False)


def main() -> None:
    app()
