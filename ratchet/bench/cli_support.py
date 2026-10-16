"""The bodies of ``ratchet bench build`` and ``ratchet bench label`` (kept out of the Typer
module so they are testable without a CLI runner)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import cast

from ratchet.bench import controls
from ratchet.bench.build import BuildPlan, build_dataset, skills_of
from ratchet.bench.dataset import BenchPaths, load_tasks
from ratchet.bench.env import BenchEnv
from ratchet.bench.estimate import Estimate, build_estimate, per_replay_usd
from ratchet.bench.inject import VARIANTS, variant_id
from ratchet.bench.labels import (
    HumanPairLabel,
    edit_diff,
    load_labels,
    record_review,
    save_labels,
)
from ratchet.bench.taxonomy import REGRESSION_TYPES
from ratchet.errors import BudgetExceeded, ConfigError
from ratchet.models import Decision

__all__ = ["build_cmd", "label_cmd", "build_plan_estimate", "parse_pairs", "check_spend"]

Echo = Callable[[str], None]
Confirm = Callable[[str], bool]
_REPLAYS_PER_EDIT = 15.0  # 10 direct goldens + 3 canaries + ~2 neighbors
_PAIR_LABELS = ("regression", "pass", "drift")


def check_spend(env: BenchEnv, est: Estimate, echo: Echo, confirm: Confirm) -> None:
    """Print the estimate; refuse live spend over the remaining cap or without a yes."""
    echo(est.render())
    if env.settings.simulated:
        echo("  (simulated models: no real spend)")
        return
    cap, spent = env.settings.ratchet_bench_usd_cap, env.construction_spent_usd()
    echo(f"  construction spend so far: ${spent:.2f} of the ${cap:.2f} bench cap")
    if est.total > cap - spent:
        raise BudgetExceeded(
            f"estimated ${est.total:.2f} exceeds the remaining bench budget ${cap - spent:.2f}; "
            "narrow it (--limit, --types, --skills) or raise RATCHET_BENCH_USD_CAP",
            code="budget_exceeded",
        )
    if not confirm(f"Spend about ${est.total:.2f} on live models?"):
        raise ConfigError("live spend not confirmed (pass --yes)", code="confirmation_required")


def build_plan_estimate(env: BenchEnv, plan: BuildPlan) -> Estimate:
    manifest = env.manifest()
    labeled = load_labels(env.labels_path).by_id()
    n_goldens = sum(1 for t in plan.tasks if plan.force or t.task_id not in manifest)
    inject_skills = plan.inject_skills if plan.inject_skills is not None else plan.skills
    variants = [
        variant_id(t, s, v)
        for s in inject_skills
        for t in plan.types
        for v in range(1, plan.variants + 1)
    ]
    n_inject = sum(1 for v in variants if plan.force or v not in labeled)
    d1 = [s for s in controls.D1_SPECS if s.skill in plan.skills]
    n_d1 = sum(1 for s in d1 if plan.force or s.edit_id not in labeled)
    replay_usd, _ = per_replay_usd(env.store, list(manifest.values()))
    return build_estimate(
        n_goldens=n_goldens if plan.goldens else 0,
        n_inject_variants=n_inject if plan.inject else 0,
        replays_per_variant=_REPLAYS_PER_EDIT,
        n_d1=n_d1 if plan.controls else 0,
        replay_usd=replay_usd,
    )


async def build_cmd(
    env: BenchEnv,
    skills: Sequence[str] | None,
    types: Sequence[str] | None,
    opts: dict[str, bool],
    echo: Echo,
    confirm: Confirm,
) -> None:
    tasks = load_tasks(env.paths.tasks)
    if skills:
        tasks = [t for t in tasks if t.canary or t.expected_skill in set(skills)]
    wanted = [t for t in REGRESSION_TYPES if types is None or t in set(types)]
    plan = BuildPlan(
        tasks=tasks,
        skills=list(skills) if skills else skills_of(tasks),
        goldens=opts.get("goldens", True),
        controls=opts.get("controls", True),
        inject=opts.get("inject", True),
        types=wanted,
        variants=VARIANTS,
        force=opts.get("force", False),
    )
    check_spend(env, build_plan_estimate(env, plan), echo, confirm)
    report = await build_dataset(env, plan, echo=echo)
    echo(
        f"goldens: {report.goldens_recorded} recorded, {report.goldens_skipped} kept; "
        f"labels: {len(report.labeled)}; dropped: {len(report.dropped)}"
    )
    if report.misrouted:
        echo(f"  goldens that ran another skill than expected: {', '.join(report.misrouted)}")


def parse_pairs(items: Sequence[str]) -> dict[str, HumanPairLabel]:
    out: dict[str, HumanPairLabel] = {}
    for item in items:
        task_id, sep, value = item.partition("=")
        if not sep or value not in _PAIR_LABELS:
            raise ConfigError(f"--pair must be task_id=regression|pass|drift, got {item!r}")
        out[task_id.strip()] = cast(HumanPairLabel, value)
    return out


def _show(paths: BenchPaths, edit_id: str, echo: Echo) -> None:
    lab = load_labels(paths.labels).get(edit_id)
    echo(f"{lab.edit_id}  [{lab.type}] skill {lab.skill}  source {lab.source}")
    echo(f"  summary:  {lab.change_summary or '—'}")
    affected = ", ".join(lab.affected_goldens) or "none"
    echo(f"  expected: {lab.expected_decision}; affected goldens: {affected}")
    echo(f"  review:   {lab.review.status} (required: {lab.review.required})")
    echo(edit_diff(paths.edits, paths.skills, edit_id) or "  (no diff)")


def label_cmd(
    paths: BenchPaths,
    edit_id: str | None,
    *,
    agree: bool | None,
    decision: str | None,
    pairs: Sequence[str],
    reviewer: str,
    note: str,
    status_only: bool,
    echo: Echo,
    interactive: bool,
    ask: Callable[[str], str] = input,
) -> None:
    labels = load_labels(paths.labels)
    pending = [
        lab for lab in labels.labels if lab.review.required and lab.review.status == "pending"
    ]
    if status_only:
        required = sum(1 for lab in labels.labels if lab.review.required)
        echo(f"review queue: {len(pending)} pending of {required} required")
        for lab in pending:
            echo(f"  {lab.edit_id}  [{lab.type}] {lab.skill}")
        return
    target = edit_id or (pending[0].edit_id if pending else None)
    if target is None:
        echo("nothing pending review")
        return
    _show(paths, target, echo)
    if agree is None and interactive:
        answer = ask("Agree with this label? [y/n/skip] ").strip().lower()
        if answer in ("y", "n"):
            agree = answer == "y"
            if not agree:
                decision = ask("Correct decision (BLOCK/WARN/PASS): ").strip().upper()
    if agree is None:
        return
    if decision is not None and decision not in ("BLOCK", "WARN", "PASS"):
        raise ConfigError("decision must be BLOCK, WARN or PASS", code="bad_args")
    labels = record_review(
        labels,
        target,
        agree=agree,
        reviewer=reviewer,
        decision=cast(Decision | None, decision),
        note=note,
        pair_labels=parse_pairs(pairs),
    )
    save_labels(paths.labels, labels)
    echo(f"recorded: {target} {'agreed' if agree else 'disagreed'} by {reviewer}")
