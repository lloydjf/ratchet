"""``ratchet bench build``: the whole dataset construction, resumable step by step.

1. goldens: record + bless every task (``goldens.record_goldens``)
2. base Tavily fixtures for every recorded fact (``controls.fact_fixtures``)
3. N0 controls (deterministic) and D1 controls (world overlay + Tavily; replayed to find the
   goldens the moved fact reaches)
4. R1–R6 injection (``inject.inject_one``; Ultra, with the manifestation check)
5. the human review sample (20% of regressions, 100% of controls)

Labels are saved after every edit, so an interrupted build resumes where it stopped: an edit
already labeled is skipped unless ``force``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta

from ratchet.bench import controls
from ratchet.bench.dataset import BenchTask
from ratchet.bench.env import BenchEnv
from ratchet.bench.execute import EditSpec, make_orchestrator, replay_only
from ratchet.bench.inject import VARIANTS, Dropped, inject_one, variant_id
from ratchet.bench.labels import (
    EditLabel,
    LabelSet,
    load_labels,
    mark_review_sample,
    save_labels,
    upsert,
    write_edit,
)
from ratchet.bench.taxonomy import REGRESSION_TYPES, EditType
from ratchet.judge.cascade import CascadeConfig
from ratchet.llm.client import ModelClient
from ratchet.models import Trace
from ratchet.orchestrator.demo import DEFAULT_FROZEN
from ratchet.orchestrator.service import Orchestrator

__all__ = ["BuildPlan", "BuildReport", "build_dataset", "skills_of", "world_goldens"]

Echo = Callable[[str], None]


@dataclass(frozen=True)
class BuildPlan:
    tasks: Sequence[BenchTask]
    skills: Sequence[str]
    goldens: bool = True
    controls: bool = True
    inject: bool = True
    types: Sequence[EditType] = REGRESSION_TYPES
    variants: int = VARIANTS
    n0_skills: Sequence[str] | None = None  # default: every skill
    d1_ids: Sequence[str] | None = None  # default: every D1 spec of the planned skills
    inject_skills: Sequence[str] | None = None  # default: every skill
    force: bool = False
    review_seed: int = 0


@dataclass
class BuildReport:
    goldens_recorded: int = 0
    goldens_skipped: int = 0
    misrouted: list[str] = field(default_factory=list)
    labeled: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def skills_of(tasks: Sequence[BenchTask]) -> list[str]:
    return sorted({t.expected_skill for t in tasks if t.expected_skill})


def world_goldens(env: BenchEnv, skill: str | None = None) -> list[Trace]:
    rows = env.store.list_traces(kind="golden", include_archived=False, limit=1_000_000)
    traces = [env.store.load_trace(r.run_id) for r in rows if r.world_dependent]
    return [t for t in traces if skill is None or skill in t.skills_invoked]


def _as_of(env: BenchEnv) -> str:
    """The Tavily fixtures' date: the day after the world's frozen time ("today" at replay)."""
    from ratchet.mocktools.world import World

    frozen = World.load(env.paths.world).frozen_time
    return ((frozen or DEFAULT_FROZEN) + timedelta(days=1)).date().isoformat()


def _keep(env: BenchEnv, labels: LabelSet, label: EditLabel) -> LabelSet:
    labels = upsert(labels, label)
    save_labels(env.labels_path, labels)
    return labels


def _n0(env: BenchEnv, labels: LabelSet, skill: str, force: bool) -> LabelSet:
    original = (env.paths.skills / skill / "SKILL.md").read_text(encoding="utf-8")
    for variant in range(1, controls.N0_VARIANTS + 1):
        edit_id = f"n0-{skill}-{variant}"
        if edit_id in labels.by_id() and not force:
            continue
        text, summary = controls.cosmetic(original, variant)
        write_edit(env.edits_root, edit_id, {f"{skill}/SKILL.md": text})
        label = EditLabel(
            edit_id=edit_id,
            skill=skill,
            type="N0",
            expected_decision="PASS",
            change_summary=summary,
            source="deterministic",
        )
        labels = _keep(env, labels, label)
    return labels


async def _d1(
    env: BenchEnv, orch: Orchestrator, labels: LabelSet, spec: controls.D1Spec, force: bool
) -> LabelSet:
    if spec.edit_id in labels.by_id() and not force:
        return labels
    path = f"{spec.skill}/SKILL.md"
    trigger = controls.d1_trigger((env.paths.skills / path).read_text(encoding="utf-8"))
    world = controls.world_override(env.paths.world, spec)
    goldens = await asyncio.to_thread(world_goldens, env, spec.skill)
    fixtures = controls.fact_fixtures(goldens, as_of=_as_of(env), moved=spec)
    root = write_edit(env.edits_root, spec.edit_id, {path: trigger}, world=world)
    controls.write_fixtures(root / "tavily", fixtures)
    divergences = await replay_only(
        env,
        orch,
        EditSpec(spec.edit_id, spec.skill, {path: trigger}, root / "world", root / "tavily"),
    )
    task_for_run = env.task_for_run()
    affected = sorted(
        task_for_run.get(d.golden_run_id, d.golden_run_id) for d in divergences if d.diverged
    )
    label = EditLabel(
        edit_id=spec.edit_id,
        skill=spec.skill,
        type="D1",
        affected_goldens=affected,
        expected_decision="PASS",
        expected_pair_outcomes=dict.fromkeys(affected, "drift"),
        change_summary=spec.summary,
        source="deterministic",
        manifested=bool(affected),
    )
    return _keep(env, labels, label)


async def build_dataset(env: BenchEnv, plan: BuildPlan, *, echo: Echo) -> BuildReport:
    from ratchet.bench.goldens import record_goldens

    report = BuildReport()
    if plan.goldens:
        done = await record_goldens(
            env, plan.tasks, force=plan.force, on_task=lambda t, r: echo(f"  golden {t.task_id}")
        )
        report.goldens_recorded, report.goldens_skipped = done.recorded, done.skipped
        report.misrouted = done.misrouted
    base = controls.fact_fixtures(await asyncio.to_thread(world_goldens, env), as_of=_as_of(env))
    controls.write_fixtures(env.tavily_dir, base)
    labels = load_labels(env.labels_path)
    orch = make_orchestrator(env, CascadeConfig())
    try:
        if plan.controls:
            for skill in plan.n0_skills if plan.n0_skills is not None else plan.skills:
                labels = _n0(env, labels, skill, plan.force)
            wanted = set(plan.d1_ids) if plan.d1_ids is not None else None
            for spec in controls.D1_SPECS:
                if spec.skill in plan.skills and (wanted is None or spec.edit_id in wanted):
                    echo(f"  control {spec.edit_id}")
                    labels = await _d1(env, orch, labels, spec, plan.force)
        if plan.inject:
            labels = await _inject_all(env, orch, labels, plan, report, echo)
    finally:
        await orch.stop()
    labels = mark_review_sample(labels, seed=plan.review_seed)
    save_labels(env.labels_path, labels)
    report.labeled = [lab.edit_id for lab in labels.labels]
    return report


async def _inject_all(
    env: BenchEnv,
    orch: Orchestrator,
    labels: LabelSet,
    plan: BuildPlan,
    report: BuildReport,
    echo: Echo,
) -> LabelSet:
    client = ModelClient(
        run_id="bench_inject", settings=env.settings, http=orch.services.judge_http
    )
    for skill in plan.inject_skills if plan.inject_skills is not None else plan.skills:
        for edit_type in plan.types:
            for variant in range(1, plan.variants + 1):
                edit_id = variant_id(edit_type, skill, variant)
                if edit_id in labels.by_id() and not plan.force:
                    continue
                echo(f"  inject {edit_id}")
                got = await inject_one(
                    env, orch, client, skill=skill, edit_type=edit_type, variant=variant
                )
                if isinstance(got, Dropped):
                    report.dropped.append(edit_id)
                    kept = [d for d in labels.dropped if d.get("edit_id") != edit_id]
                    labels = labels.model_copy(update={"dropped": [*kept, got.as_dict()]})
                    save_labels(env.labels_path, labels)
                else:
                    labels = _keep(env, labels, got)
    return labels
