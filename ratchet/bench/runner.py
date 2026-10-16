"""``ratchet bench run``: one configuration over the labeled edits.

load labels → plan the jobs (``--types``, a seeded stratified ``--limit``, skipping edits
already in ``results.json``) → print the cost estimate and check it against the remaining bench
budget → run each edit through the replay pipeline in process (``execute.run_full``, or
``execute.rejudge`` for a judge-only ablation that reuses another run's replays) → store each
verdict as it lands → metrics → ``results.json`` + ``report.md``.

E6 (``mode: noise``) replays every skill's goldens ``noise_repeats`` times with the
*unchanged* skill. Control replays (ADR 0023, ``cascade.controls``) are on unless a config
turns them off (``controls_off.yaml``); a judge-only re-judge reuses the source run's
control results instead of replaying controls of its own. Live spend is refused unless
confirmed (``--yes``) and refused outright when the estimate exceeds what is left of
``ratchet_bench_usd_cap`` in the bench store; a running sweep stops scheduling once the cap
is reached.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from ratchet import clock
from ratchet.bench import estimate
from ratchet.bench.dataset import BenchConfig, BenchTask, load_config, load_tasks
from ratchet.bench.env import BenchEnv
from ratchet.bench.execute import EditSpec, Outcome, make_orchestrator, rejudge, run_full
from ratchet.bench.labels import EditLabel, edit_files, edit_tavily, edit_world, load_labels
from ratchet.bench.results import (
    EditResult,
    ResultsFile,
    RunResult,
    edit_result,
    load_results,
    make_meta,
    save_results,
    with_prompt_tokens,
)
from ratchet.errors import BudgetExceeded, ConfigError
from ratchet.log import get_logger
from ratchet.orchestrator.context import CANARIES_FILE
from ratchet.orchestrator.service import Orchestrator

__all__ = ["RunOptions", "Job", "select_labels", "noise_labels", "plan_jobs", "run_config"]

Echo = Callable[[str], None]
Confirm = Callable[[str], bool]
_NEIGHBORS = 2.0  # assumed trigger-neighborhood goldens per edit, for the estimate
_log = get_logger("bench")


@dataclass(frozen=True)
class RunOptions:
    config_path: Path
    out: Path
    limit: int | None = None
    types: Sequence[str] | None = None
    seed: int = 0
    live_tavily: bool = False
    concurrency: int | None = None
    command: str = ""


@dataclass(frozen=True)
class Job:
    label: EditLabel
    spec: EditSpec
    repeat: int = 0
    source_edit_id: str | None = None


@dataclass
class _State:
    results: ResultsFile
    run: RunResult
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stopped: bool = False


def select_labels(
    labels: Sequence[EditLabel], *, types: Sequence[str] | None, limit: int | None, seed: int
) -> list[EditLabel]:
    """Labels of the wanted types; ``limit`` takes a seeded sample stratified by type."""
    pool = [lab for lab in labels if types is None or lab.type in set(types)]
    by_type: dict[str, list[EditLabel]] = {}
    for lab in sorted(pool, key=lambda x: x.edit_id):
        by_type.setdefault(lab.type, []).append(lab)
    rng = random.Random(seed)
    for group in by_type.values():
        rng.shuffle(group)
    ordered: list[EditLabel] = []
    while any(by_type.values()):
        for key in sorted(by_type):
            if by_type[key]:
                ordered.append(by_type[key].pop(0))
    return ordered if limit is None else ordered[:limit]


def noise_labels(skills: Sequence[str], repeats: int) -> list[tuple[EditLabel, int]]:
    out: list[tuple[EditLabel, int]] = []
    for k in range(1, repeats + 1):  # repeat-major, so --limit N covers every skill first
        for skill in skills:
            label = EditLabel(
                edit_id=f"noise-{skill}-{k}",
                skill=skill,
                type="N0",
                expected_decision="PASS",
                change_summary="E6: unchanged skill, replayed",
                source="deterministic",
            )
            out.append((label, k))
    return out


def _spec(env: BenchEnv, label: EditLabel) -> EditSpec:
    return EditSpec(
        bench_id=label.edit_id,
        skill=label.skill,
        files=edit_files(env.edits_root, label.edit_id),
        world_dir=edit_world(env.edits_root, label.edit_id),
        tavily_dir=edit_tavily(env.edits_root, label.edit_id),
    )


def plan_jobs(
    env: BenchEnv, config: BenchConfig, results: ResultsFile, opts: RunOptions
) -> list[Job]:
    run = results.runs.get(config.name)
    done = run.done_ids() if run else set()
    if config.mode == "noise":
        skills = sorted({t.expected_skill for t in _tasks(env) if t.expected_skill})
        noise = [
            Job(lab, EditSpec(lab.edit_id, lab.skill, noise=True), repeat=k)
            for lab, k in noise_labels(skills, config.noise_repeats)
        ]
        jobs = [j for j in noise if j.label.edit_id not in done]
        return jobs if opts.limit is None else jobs[: opts.limit]
    labels = load_labels(env.labels_path).labels
    types = [t for t in config.types if opts.types is None or t in set(opts.types)]
    chosen = select_labels(labels, types=types, limit=opts.limit, seed=opts.seed)
    source = results.runs.get(config.reuse_replays) if config.reuse_replays else None
    by_id = {e.bench_id: e.edit_id for e in source.edits if e.edit_id} if source else {}
    return [
        Job(lab, _spec(env, lab), source_edit_id=by_id.get(lab.edit_id))
        for lab in chosen
        if lab.edit_id not in done
    ]


def _tasks(env: BenchEnv) -> list[BenchTask]:
    manifest = env.manifest()
    return [t for t in load_tasks(env.paths.tasks) if t.task_id in manifest]


def _estimate(env: BenchEnv, config: BenchConfig, jobs: Sequence[Job]) -> estimate.Estimate:
    manifest = env.manifest()
    tasks = load_tasks(env.paths.tasks)
    canaries_path = env.store.data_dir / CANARIES_FILE
    n_canaries = len(json.loads(canaries_path.read_text("utf-8"))) if canaries_path.is_file() else 3
    direct: dict[str, int] = {}
    for t in tasks:
        if t.expected_skill and t.task_id in manifest:
            direct[t.expected_skill] = direct.get(t.expected_skill, 0) + 1
    sizes = [direct.get(j.label.skill, 10) + n_canaries + _NEIGHBORS for j in jobs]
    pairs = min(sum(sizes) / len(sizes), config.max_traces) if sizes else 0.0
    replay_usd, source = estimate.per_replay_usd(env.store, list(manifest.values()))
    reuse = bool(jobs) and all(j.source_edit_id for j in jobs)
    return estimate.sweep_estimate(
        name=config.name,
        n_edits=len(jobs),
        pairs_per_edit=pairs,
        mode=config.cascade.mode,
        drift=config.cascade.drift,
        replay_usd=replay_usd,
        replay_source=source,
        reuse_replays=reuse,
    )


def _guard(env: BenchEnv, est: estimate.Estimate, echo: Echo, confirm: Confirm) -> None:
    cap = env.settings.ratchet_bench_usd_cap
    spent = env.sweep_spent_usd()
    echo(est.render())
    if env.settings.simulated:
        echo("  (simulated models: no real spend; the figures above are simulator usage)")
        return
    echo(f"  sweep spend so far: ${spent:.2f} of the ${cap:.2f} bench cap")
    if est.total > cap - spent:
        raise BudgetExceeded(
            f"estimated ${est.total:.2f} exceeds the remaining bench budget ${cap - spent:.2f}; "
            "narrow it (--limit, --types, --skills) or raise RATCHET_BENCH_USD_CAP",
            code="budget_exceeded",
        )
    if not confirm(f"Spend about ${est.total:.2f} on live models?"):
        raise ConfigError("live spend not confirmed (pass --yes)", code="confirmation_required")


def _start(
    results: ResultsFile | None, env: BenchEnv, config: BenchConfig, opts: RunOptions
) -> _State:
    labels = load_labels(env.labels_path)
    if results is None:
        meta = make_meta(
            env.settings, labels_path=str(env.labels_path), n_labels=len(labels.labels)
        )
        results = ResultsFile(meta=meta)
    now = clock.now()
    run = results.runs.get(config.name) or RunResult(config=config, started_at=now, updated_at=now)
    run = run.model_copy(
        update={"config": config, "config_path": str(opts.config_path), "command": opts.command}
    )
    return _State(results=results, run=run)


async def _record(state: _State, out: Path, result: EditResult) -> None:
    async with state.lock:
        kept = [e for e in state.run.edits if e.bench_id != result.bench_id]
        state.run = state.run.model_copy(
            update={"edits": [*kept, result], "updated_at": clock.now()}
        )
        meta = state.results.meta.model_copy(update={"updated_at": clock.now()})
        runs = {**state.results.runs, state.run.config.name: state.run}
        state.results = state.results.model_copy(update={"meta": meta, "runs": runs})
        await asyncio.to_thread(save_results, out / "results.json", state.results)


async def run_config(
    env: BenchEnv, opts: RunOptions, *, echo: Echo, confirm: Confirm
) -> ResultsFile:
    config = load_config(opts.config_path)
    opts.out.mkdir(parents=True, exist_ok=True)
    state = _start(load_results(opts.out / "results.json"), env, config, opts)
    jobs = plan_jobs(env, config, state.results, opts)
    echo(f"{config.name}: {len(jobs)} edits to run ({len(state.run.done_ids())} already done)")
    if not jobs:
        return state.results
    _guard(env, _estimate(env, config, jobs), echo, confirm)
    orch = make_orchestrator(
        env,
        config.cascade.to_config(),
        runner=config.runner,
        live_tavily=opts.live_tavily,
        max_traces=config.max_traces,
        controls=config.cascade.controls,
    )
    sem = asyncio.Semaphore(opts.concurrency or config.concurrency)
    task_for_run = env.task_for_run()

    async def one(job: Job) -> None:
        async with sem:
            if state.stopped or _over_cap(env):
                state.stopped = True
                return
            outcome = await _execute(env, orch, job)
            result = edit_result(
                job.label,
                outcome.verdict,
                edit_id=outcome.edit_id,
                state=outcome.state,
                latency_s=outcome.latency_s,
                task_for_run=task_for_run,
                single_judge_blocks=config.blocks_on_single_judge,
                repeat=job.repeat,
            )
            if outcome.edit_id:
                totals = await asyncio.to_thread(env.store.spend, edit_id=outcome.edit_id)
                result = with_prompt_tokens(result, totals)
            await _record(state, opts.out, result)
            echo(f"  {job.label.edit_id:<38} {result.decision or result.error}")

    try:
        await asyncio.gather(*(one(job) for job in jobs))
    finally:
        await orch.stop()
    if state.stopped:
        echo("  stopped: the bench cap was reached")
    return state.results


def _over_cap(env: BenchEnv) -> bool:
    if env.settings.simulated:
        return False
    return env.sweep_spent_usd() >= env.settings.ratchet_bench_usd_cap


async def _execute(env: BenchEnv, orch: Orchestrator, job: Job) -> Outcome:
    try:
        if job.source_edit_id:
            return await rejudge(env, orch, job.spec, job.source_edit_id)
        return await run_full(env, orch, job.spec)
    except Exception as exc:  # noqa: BLE001 - one broken edit never stops the sweep
        _log.error("bench.edit.crashed", bench_id=job.label.edit_id, error_type=type(exc).__name__)
        return Outcome("", f"crashed:{type(exc).__name__}", None, 0.0)
