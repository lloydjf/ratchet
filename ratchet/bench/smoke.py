"""``ratchet bench smoke``: the whole bench pipeline on the offline simulator.

Always simulated (ADR 0018): the upstream is forced to ``sim``, every key is dropped, Tavily
is fixtures-only, and the report carries the "SIMULATED MODELS — pipeline smoke test; not
benchmark results" banner. It uses the smoke subset of ``bench/tasks.yaml`` (3–4 tasks for each
of the 5 skills with simulator policies, plus the 3 canaries) and:

1. records + blesses those goldens; writes base Tavily fixtures;
2. labels the hand-written edits of ``bench/smoke/edits.yaml`` (replayed to find the goldens
   they reach), generates N0 + D1 controls, and runs the simulator's deterministic
   ``inject_regression`` for R5/R6 on one skill (exercising ``inject.py``);
3. runs every configuration of ``bench/configs/`` (the ablations re-judge the cascade's
   replays), E6 included;
4. writes ``results.json``, ``report.md``, ``cost_vs_recall.svg`` and ``labels.json``.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from ratchet.bench.build import BuildPlan, build_dataset, skills_of
from ratchet.bench.dataset import BenchPaths, load_tasks
from ratchet.bench.env import BenchEnv
from ratchet.bench.execute import EditSpec, make_orchestrator, replay_only
from ratchet.bench.labels import EditLabel, edit_files, load_labels, save_labels, upsert
from ratchet.bench.publish import write_report
from ratchet.bench.results import ResultsFile, load_results, save_results
from ratchet.bench.runner import RunOptions, run_config
from ratchet.bench.taxonomy import expected_affected_outcome, expected_decision
from ratchet.config.settings import Settings, configure
from ratchet.errors import ConfigError
from ratchet.judge.cascade import CascadeConfig

__all__ = ["SmokeOptions", "CONFIG_ORDER", "smoke_settings", "run_smoke", "label_hand_edits"]

Echo = Callable[[str], None]
CONFIG_ORDER = (
    "noise",
    "cascade",
    "nano_only",
    "super_only",
    "ultra_only",
    "drift_off",
    "level0_off",
)
_N0_SKILLS = ("github-triage", "fx-expense-converter")
_D1 = ("d1-fx-expense-converter-1", "d1-fx-expense-converter-2")
_INJECT = ("expense-report",)


@dataclass(frozen=True)
class SmokeOptions:
    work: Path
    out: Path
    configs: Sequence[str] = CONFIG_ORDER
    inject: bool = True


def smoke_settings(base: Settings) -> Settings:
    """``base`` forced onto the simulator: no keys, fixture Tavily, local process runner."""
    return base.model_copy(
        update={
            "ratchet_upstream": "sim",
            "nebius_token_factory_key": None,
            "tavily_api_key": None,
            "nebius_api_key": None,
            "ratchet_tavily_mode": "fixtures",
            "ratchet_job_runner": "local",
            "ratchet_local_runner_mode": "process",
            "ratchet_sandbox": "process",
            "ratchet_harness": "minimal",
            "ratchet_selection_embedder": "local",
            "ratchet_log_level": "WARNING",
        }
    )


async def label_hand_edits(env: BenchEnv, manifest: Path, source: Path, *, echo: Echo) -> None:
    """Copy hand-written edits in, replay each to find the goldens it reaches, label it."""
    entries = (yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}).get("edits", [])
    labels = load_labels(env.labels_path)
    orch = make_orchestrator(env, CascadeConfig())
    task_for_run = env.task_for_run()
    try:
        for entry in entries:
            edit_id, edit_type = str(entry["edit_id"]), str(entry["type"])
            shutil.copytree(source / edit_id, env.edits_root / edit_id, dirs_exist_ok=True)
            files = edit_files(env.edits_root, edit_id)
            divergences = await replay_only(
                env, orch, EditSpec(edit_id, str(entry["skill"]), files)
            )
            affected = sorted(
                task_for_run.get(d.golden_run_id, d.golden_run_id)
                for d in divergences
                if d.diverged
            )
            echo(f"  hand edit {edit_id}: {len(affected)} goldens diverge")
            label = EditLabel.model_validate(
                {
                    "edit_id": edit_id,
                    "skill": entry["skill"],
                    "type": edit_type,
                    "affected_goldens": affected,
                    "expected_decision": expected_decision(edit_type),
                    "expected_pair_outcomes": dict.fromkeys(
                        affected, expected_affected_outcome(edit_type)
                    ),
                    "change_summary": entry.get("change_summary", ""),
                    "source": "hand",
                    "manifested": bool(affected),
                }
            )
            labels = upsert(labels, label)
            save_labels(env.labels_path, labels)
    finally:
        await orch.stop()


def _point_meta_at(out: Path) -> None:
    """The smoke's labels live in a throwaway work dir; cite the copy next to the report."""
    results = load_results(out / "results.json")
    if results is not None:
        meta = results.meta.model_copy(update={"labels_path": str(out / "labels.json")})
        save_results(out / "results.json", results.model_copy(update={"meta": meta}))


async def run_smoke(
    paths: BenchPaths, base: Settings, opts: SmokeOptions, *, echo: Echo
) -> ResultsFile:
    settings = smoke_settings(base)
    if not settings.simulated:
        raise ConfigError("the bench smoke test only runs on the simulator", code="config_error")
    shutil.rmtree(opts.work, ignore_errors=True)
    opts.out.mkdir(parents=True, exist_ok=True)
    (opts.out / "results.json").unlink(missing_ok=True)
    env = BenchEnv.open(
        paths,
        opts.work,
        settings,
        edits_root=opts.work / "edits",
        labels_path=opts.work / "labels.json",
        tavily_dir=opts.work / "tavily_fixtures",
    )
    configure(env.settings)
    tasks = [t for t in load_tasks(paths.tasks) if t.smoke]
    skills = skills_of(tasks)
    echo(f"smoke (SIMULATED): {len(tasks)} goldens over {len(skills)} skills")
    plan = BuildPlan(
        tasks=tasks,
        skills=skills,
        inject=opts.inject,
        types=("R5", "R6"),
        variants=1,
        n0_skills=_N0_SKILLS,
        d1_ids=_D1,
        inject_skills=_INJECT,
    )
    await build_dataset(env, plan, echo=echo)
    await label_hand_edits(
        env, paths.root / "smoke" / "edits.yaml", paths.root / "smoke" / "edits", echo=echo
    )
    results: ResultsFile | None = None
    for name in opts.configs:
        run_opts = RunOptions(
            config_path=paths.configs / f"{name}.yaml",
            out=opts.out,
            command=f"ratchet bench smoke  # config {name}",
        )
        results = await run_config(env, run_opts, echo=echo, confirm=lambda _: True)
    shutil.copyfile(env.labels_path, opts.out / "labels.json")
    _point_meta_at(opts.out)
    write_report(
        opts.out,
        labels_path=env.labels_path,
        edits_root=env.edits_root,
        skills_root=paths.skills,
    )
    assert results is not None
    return results
