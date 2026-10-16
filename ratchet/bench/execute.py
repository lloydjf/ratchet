"""Run one bench edit through the replay pipeline, in process (design §3).

Every edit lands on a scratch ``clone_to`` copy of the bench repo as one ``agent`` commit, the
way an agent's own skill edit would, and runs through the orchestrator's real state machine
(``pipeline.run_edit``: selection → replay batches on the local or Nebius runner → Level 0 →
cascade → drift → decide → narrative → act). A D1 case also gets a world overlay
(``orchestrator.overlay.build``) and its own Tavily fixtures. A noise-floor (E6) run replays
the *unchanged* skill: an edit whose commit is ``main`` itself.

:func:`replay_only` stops after ``collecting`` (no judge, no spend beyond the replays) and
reports which goldens diverged — the manifestation check for injected variants.
"""

from __future__ import annotations

import asyncio
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from ratchet import clock
from ratchet.bench.env import BenchEnv, tavily_factory
from ratchet.judge import level0
from ratchet.judge.cascade import CascadeConfig
from ratchet.log import get_logger
from ratchet.models import SkillEdit, Trace, Verdict
from ratchet.orchestrator import overlay
from ratchet.orchestrator.context import (
    EditOptions,
    attempt_dir,
    build_context,
    load_options,
    save_options,
)
from ratchet.orchestrator.ids import batch_id_for, retry_batch_id_for, verdict_id_for
from ratchet.orchestrator.judging import collect_replays
from ratchet.orchestrator.pipeline import advance, run_edit
from ratchet.orchestrator.service import Orchestrator
from ratchet.store import BatchRow, Store
from ratchet.util import new_id
from ratchet.watcher.edits import build_edit
from ratchet.watcher.gitops import SkillsRepo

__all__ = [
    "EditSpec",
    "Divergence",
    "Outcome",
    "make_orchestrator",
    "prepare",
    "run_full",
    "rejudge",
    "replay_only",
    "divergence_reasons",
]

_log = get_logger("bench")
_REPLAY_STATES = ("selecting", "dispatching", "collecting")


@dataclass(frozen=True)
class EditSpec:
    bench_id: str
    skill: str
    files: dict[str, str | None] = field(default_factory=dict)
    world_dir: Path | None = None
    tavily_dir: Path | None = None
    noise: bool = False


@dataclass(frozen=True)
class Divergence:
    golden_run_id: str
    replay_run_id: str | None
    reasons: list[str]
    selected_as: str = "direct"  # direct | neighborhood | canary

    @property
    def diverged(self) -> bool:
        return bool(self.reasons) and self.reasons != ["replay_failed"]


@dataclass(frozen=True)
class Outcome:
    edit_id: str
    state: str
    verdict: Verdict | None
    latency_s: float


def make_orchestrator(
    env: BenchEnv,
    cascade: CascadeConfig,
    *,
    runner: str = "local",
    live_tavily: bool = False,
    max_traces: int = 40,
    controls: bool = True,
) -> Orchestrator:
    """An orchestrator over the bench store; only its ``services`` are used (never started).
    ``controls`` is the configuration's ADR 0023 control-replay switch."""
    settings = env.settings.model_copy(
        update={
            "ratchet_job_runner": runner,
            "ratchet_feature_drift": True,
            "ratchet_feature_control_replays": controls,
            "ratchet_max_traces_per_edit": max_traces,
        }
    )
    return Orchestrator(
        settings,
        env.store,
        cascade=cascade,
        tavily_factory=tavily_factory(live_tavily),
        poll_s=0.25,
        submit_backoff_s=(1.0, 2.0),
        digest_enabled=False,
    )


def _world_ref(env: BenchEnv, spec: EditSpec) -> str | None:
    if spec.world_dir is None:
        return None
    rows = env.store.list_traces(kind="golden", limit=1_000_000)
    goldens = [env.store.load_trace(r.run_id) for r in rows if r.world_dependent]
    built = overlay.build(
        env.store,
        env.settings,
        f"bench-{spec.bench_id}",
        spec.world_dir,
        goldens,
        base_world=env.paths.world,
    )
    return str(built)


def _noise_edit(repo: SkillsRepo, skill: str) -> tuple[SkillEdit, str]:
    head = repo.main_head()
    edit = SkillEdit(
        edit_id=new_id("edit"),
        commit=head,
        parent=head,
        files=[f"{skill}/SKILL.md"],
        diff_ref="",
        affected_skills=[skill],
        author="agent",
        detected_at=clock.now(),
        summary=f"{skill}: unchanged (noise-floor replay)",
    )
    return edit, ""


def prepare(env: BenchEnv, spec: EditSpec) -> tuple[str, SkillsRepo]:
    """Commit the edit on a scratch repo and save it with its options; returns the edit id."""
    repo = env.scratch_repo()
    if spec.noise:
        edit, diff = _noise_edit(repo, spec.skill)
    else:
        head = repo.apply_files(spec.files, message=f"bench: {spec.bench_id}", author="agent")
        edit, diff = build_edit(repo, commit=head, author="agent")
    saved = env.store.save_edit(edit, diff)
    options = EditOptions(
        scenario_id=None,
        repo_dir=str(repo.main_dir),
        world_ref=_world_ref(env, spec),
        tavily_dir=str(spec.tavily_dir) if spec.tavily_dir else None,
    )
    save_options(env.store, saved.edit_id, options)
    return saved.edit_id, repo


async def run_full(env: BenchEnv, orchestrator: Orchestrator, spec: EditSpec) -> Outcome:
    """The replay pipeline end to end; the verdict is ``None`` only if the pipeline wrote none."""
    edit_id, repo = await asyncio.to_thread(prepare, env, spec)
    started = time.monotonic()
    try:
        state = await run_edit(orchestrator.services, edit_id)
    finally:
        await asyncio.to_thread(env.drop_scratch, repo)
    latency = time.monotonic() - started
    verdict: Verdict | None = None
    if env.store.verdict_path(verdict_id_for(edit_id)).is_file():
        verdict = await asyncio.to_thread(env.store.load_verdict, verdict_id_for(edit_id))
    _log.info("bench.edit.done", bench_id=spec.bench_id, edit_id=edit_id, state=state)
    return Outcome(edit_id, state, verdict, round(latency, 3))


def _clone_controls(env: BenchEnv, edit_id: str, source_edit_id: str, attempt: int) -> None:
    """ADR 0023: copy the source run's control results (``controls/<golden>.json``) and mark
    the re-judge ``reuse_controls``, so it never dispatches a control of its own. A pair the
    re-judge contests but the source run did not has no control and keeps its outcome."""
    store = env.store
    source = attempt_dir(store, source_edit_id, attempt) / "controls"
    if source.is_dir():
        shutil.copytree(source, attempt_dir(store, edit_id, 1) / "controls", dirs_exist_ok=True)
    options = load_options(store, edit_id)
    save_options(store, edit_id, options.model_copy(update={"reuse_controls": True}))


def _clone_replays(env: BenchEnv, edit_id: str, source_edit_id: str) -> None:
    """Give ``edit_id`` the selection and finished replay batches of ``source_edit_id`` (and
    its control results) and park it at ``judging``: the pipeline then judges the same
    replays under another config."""
    store = env.store
    store.save_selection(edit_id, store.load_selection(source_edit_id))
    attempt = load_options(store, source_edit_id).attempt
    _clone_controls(env, edit_id, source_edit_id, attempt)
    rows = store.batches_for_edit(source_edit_id)
    for make_id in (batch_id_for, retry_batch_id_for):  # the retry round's results too
        prefix = make_id(source_edit_id, attempt, 0)[:-2]
        mine = [b for b in rows if b.batch_id.startswith(prefix)]
        for index, row in enumerate(mine):
            _clone_batch(store, row, make_id(edit_id, 1, index), edit_id)
    store.set_edit_state(edit_id, "judging")


def _clone_batch(store: Store, row: BatchRow, new_id: str, edit_id: str) -> None:
    spec = store.load_batch_spec(row.batch_id)
    store.save_batch_spec(
        spec.model_copy(update={"batch_id": new_id, "edit_id": edit_id}), row.runner
    )
    result = store.load_batch_result(row.batch_id)
    if result is not None:
        store.save_batch_result(result.model_copy(update={"batch_id": new_id}))
    store.update_batch(
        new_id,
        status=row.status,
        job_id=f"reused:{row.batch_id}",
        progress_k=row.progress_k,
        progress_n=row.progress_n,
        finished_at=clock.now(),
    )


async def rejudge(
    env: BenchEnv, orchestrator: Orchestrator, spec: EditSpec, source_edit_id: str
) -> Outcome:
    """Judge the replays of an earlier run of the same edit under ``orchestrator``'s config."""
    edit_id, repo = await asyncio.to_thread(prepare, env, spec)
    started = time.monotonic()
    try:
        await asyncio.to_thread(_clone_replays, env, edit_id, source_edit_id)
        state = await run_edit(orchestrator.services, edit_id)
    finally:
        await asyncio.to_thread(env.drop_scratch, repo)
    verdict: Verdict | None = None
    if env.store.verdict_path(verdict_id_for(edit_id)).is_file():
        verdict = await asyncio.to_thread(env.store.load_verdict, verdict_id_for(edit_id))
    return Outcome(edit_id, state, verdict, round(time.monotonic() - started, 3))


def _norm(text: str) -> str:
    return " ".join(text.split())


def divergence_reasons(golden: Trace, replay: Trace | None, unexpected: list[object]) -> list[str]:
    """Why a replay differs from its golden (empty = identical behavior)."""
    if replay is None:
        return ["replay_failed"]
    from ratchet.models import ToolCall

    extra = [c for c in unexpected if isinstance(c, ToolCall)]
    reasons: list[str] = []
    if not replay.harness_ok:
        reasons.append("harness_failed")
    if _norm(golden.final_output) != _norm(replay.final_output):
        reasons.append("output")
    finding = level0.diff(golden, replay, extra)
    if finding.new_calls:
        reasons.append("new_calls")
    if finding.missing_calls:
        reasons.append("missing_calls")
    if finding.changed_args:
        reasons.append("changed_args")
    return reasons


async def replay_only(
    env: BenchEnv, orchestrator: Orchestrator, spec: EditSpec
) -> list[Divergence]:
    """Replay the selected goldens against the edit and report divergence; no judging."""
    edit_id, repo = await asyncio.to_thread(prepare, env, spec)
    await asyncio.to_thread(env.mark_construction, edit_id)
    try:
        ctx = await asyncio.to_thread(build_context, orchestrator.services, edit_id)
        await asyncio.to_thread(env.store.set_edit_state, edit_id, "selecting")
        state = "selecting"
        while state in _REPLAY_STATES:
            state = await advance(ctx, state)
        items = await asyncio.to_thread(collect_replays, ctx) if state == "judging" else []
        selected = {
            sel.run_id: sel.reason
            for sel in await asyncio.to_thread(env.store.load_selection, edit_id)
        }
    finally:
        # A replay-only edit is terminal: an orchestrator resuming this store skips it.
        await asyncio.to_thread(env.store.set_edit_state, edit_id, "done")
        await asyncio.to_thread(env.drop_scratch, repo)
    return [
        Divergence(
            item.golden.run_id,
            item.replay.run_id if item.replay is not None else None,
            divergence_reasons(item.golden, item.replay, list(item.unexpected)),
            selected.get(item.golden.run_id, "direct"),
        )
        for item in items
    ]
