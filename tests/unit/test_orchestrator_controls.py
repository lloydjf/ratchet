"""Control replays (ADR 0023): the decision rule, the controlling state, restart safety,
events and the progress strip."""

from __future__ import annotations

import shutil
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import (  # noqa: E402
    T0,
    commit_edit,
    empty_finding,
    fixture_trace,
    make_settings,
    open_store,
    seeded_repo,
)

from ratchet import clock, copy  # noqa: E402
from ratchet.models import PairVerdict, ReplayBatch, ReplayResult, ToolCall, Trace  # noqa: E402
from ratchet.orchestrator import controls  # noqa: E402
from ratchet.orchestrator.context import EditContext, Services, build_context  # noqa: E402
from ratchet.orchestrator.controls import (  # noqa: E402
    apply_control,
    contested,
    control_outcome,
    control_path,
    handle_controlling,
)
from ratchet.orchestrator.embed import LocalHashEmbedder  # noqa: E402
from ratchet.orchestrator.judging import load_pairs, pair_path  # noqa: E402
from ratchet.orchestrator.progress import progress_for  # noqa: E402
from ratchet.replay.jobs.base import JobStatus  # noqa: E402
from ratchet.store import SelectedTrace  # noqa: E402
from ratchet.store.base import write_json_atomic  # noqa: E402

FIXTURE = "r2_dropped_field"


def _call(tool: str, side_effect: str = "read", step: str = "t001") -> ToolCall:
    return ToolCall.model_validate(
        {
            "step_id": step,
            "tool": tool,
            "args_norm": {},
            "args_hash": "h",
            "result_hash": None,
            "side_effect": side_effect,
            "source": "cassette",
            "cassette_key": None,
        }
    )


def _pair(outcome: str = "regression", decided_by: str = "ultra", **update: Any) -> PairVerdict:
    base = PairVerdict(
        golden_run_id="run_g",
        replay_run_id="run_r",
        level0=empty_finding("run_g", "run_r"),
        opinions=[],
        drift=[],
        outcome=outcome,  # type: ignore[arg-type]
        usd=0.01,
        decided_by=decided_by,  # type: ignore[arg-type]
        reason="Totals line is no longer met.",
    )
    return base.model_copy(update=update)


def _control(outcome: str, **update: Any) -> PairVerdict:
    found = empty_finding("run_g", "run_c")
    base = _pair(outcome, "nano", replay_run_id="run_c", level0=found, usd=0.001)
    return base.model_copy(update=update)


# --- the rule ---------------------------------------------------------------------------------
def test_a_control_that_also_regresses_makes_the_pair_pass_by_control() -> None:
    adjusted = apply_control(_pair(error_code=None), _control("regression"))
    assert adjusted.outcome == "pass" and adjusted.decided_by == "control"
    assert adjusted.reason == copy.explain_nondeterminism()
    assert adjusted.error_code is None
    assert (adjusted.control_run_id, adjusted.control_outcome) == ("run_c", "diverges")
    assert adjusted.usd == pytest.approx(0.011)


def test_a_control_with_the_same_level0_difference_diverges_even_if_nano_passes() -> None:
    sheet = _call("sheets.append", "write_reversible")
    changed = empty_finding("run_g", "run_r").model_copy(update={"changed_args": [(sheet, sheet)]})
    pair = _pair(level0=changed)
    finding = empty_finding("run_g", "run_c").model_copy(update={"missing_calls": [sheet]})
    assert control_outcome(pair, _control("pass", level0=finding)) == "diverges"
    other = finding.model_copy(update={"missing_calls": [_call("gmail.send", "external_send")]})
    assert control_outcome(pair, _control("pass", level0=other)) == "agrees"


def test_a_control_that_matches_the_golden_leaves_the_outcome_standing() -> None:
    adjusted = apply_control(_pair(), _control("pass"))
    assert adjusted.outcome == "regression" and adjusted.decided_by == "ultra"
    assert adjusted.reason == "Totals line is no longer met."
    assert (adjusted.control_run_id, adjusted.control_outcome) == ("run_c", "agrees")


@pytest.mark.parametrize("outcome", ["uncertain", "improved", "failed"])
def test_an_inconclusive_control_changes_nothing_but_the_record(outcome: str) -> None:
    control = _control(outcome)
    if outcome == "failed":
        control = control.model_copy(update={"replay_run_id": ""})
    adjusted = apply_control(_pair(), control)
    assert adjusted.outcome == "regression" and adjusted.control_outcome is None


def test_only_judged_regressions_and_uncertain_pairs_are_contested() -> None:
    assert contested(_pair("regression", "ultra"))
    assert contested(_pair("uncertain", "ultra"))
    assert not contested(_pair("regression", "level0"))  # Level 0 decided: never a control
    assert not contested(_pair("pass", "nano"))
    assert not contested(_pair("drift", "drift"))
    assert not contested(_pair("uncertain", "ultra", error_code="judge_timeout"))
    assert not contested(_pair("uncertain", "ultra", error_code="drift_unavailable"))  # Tavily down
    assert not contested(_pair("failed", "infra", error_code="harness_failed"))


# --- the controlling state --------------------------------------------------------------------
class FakeRunner:
    """Finishes every batch at once; its result holds a copy of the golden as the control."""

    name = "fake"

    def __init__(self, store: Any) -> None:
        self.store = store
        self.submits: list[ReplayBatch] = []

    async def submit(self, spec: ReplayBatch, inputs_dir: Path) -> str:
        self.submits.append(spec)
        return f"job-{spec.batch_id}"

    async def poll(self, job_id: str) -> JobStatus:
        return JobStatus("succeeded", 1, 1, clock.now(), 0)

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        spec = next(s for s in self.submits if job_id == f"job-{s.batch_id}")
        replays = []
        for run_id in spec.golden_run_ids:
            golden: Trace = self.store.load_trace(run_id)
            control = golden.model_copy(
                update={
                    "run_id": f"ctl_{run_id}",
                    "kind": "replay",
                    "golden_of": run_id,
                    "candidate_commit": spec.candidate_commit,
                }
            )
            replays.append(control)
        result = ReplayResult(
            batch_id=spec.batch_id,
            replays=replays,
            unexpected_side_effects=[],
            worker_log_ref="",
            status="ok",
        )
        write_json_atomic(output_dir / "result.json", result)
        return output_dir

    async def cancel(self, job_id: str) -> None:
        return None


def _setup(
    tmp_path: Path, outcome: str = "regression", decided_by: str = "ultra", **over: Any
) -> tuple[EditContext, FakeRunner, Trace]:
    settings = make_settings(tmp_path, ratchet_sandbox="docker", **over)
    store = open_store(settings)
    runner = FakeRunner(store)
    services = Services(
        settings=settings,
        store=store,
        runner=runner,  # type: ignore[arg-type]
        retry=runner,  # type: ignore[arg-type]
        judge_http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        embedder=LocalHashEmbedder(),
        poll_s=0.0,
    )
    repo = seeded_repo(settings)
    edit, diff = commit_edit(repo, {"expense-report/NOTE.md": "x\n"})
    store.save_edit(edit, diff)
    golden = fixture_trace(FIXTURE, "golden")
    replay = fixture_trace(FIXTURE, "replay")
    store.save_trace(golden)
    store.save_trace(replay)
    store.save_selection(edit.edit_id, [SelectedTrace(golden.run_id, "direct", 1.0)])
    ctx = build_context(services, edit.edit_id)
    judged = PairVerdict(
        golden_run_id=golden.run_id,
        replay_run_id=replay.run_id,
        level0=empty_finding(golden.run_id, replay.run_id),
        opinions=[],
        drift=[],
        outcome=outcome,  # type: ignore[arg-type]
        usd=0.02,
        decided_by=decided_by,  # type: ignore[arg-type]
    )
    write_json_atomic(pair_path(ctx, golden.run_id), judged)
    return ctx, runner, golden


def _fake_judge(outcome: str, calls: list[str]) -> Any:
    async def judge(pair_ctx: Any, _client: Any, config: Any, **_k: Any) -> PairVerdict:
        assert config.mode == "nano_only"  # never Super or Ultra for a control
        calls.append(pair_ctx.replay.run_id)
        return _control(outcome).model_copy(
            update={
                "golden_run_id": pair_ctx.golden.run_id,
                "replay_run_id": pair_ctx.replay.run_id,
            }
        )

    return judge


def _kinds(ctx: EditContext, kind: str) -> list[dict[str, object]]:
    return [e.payload for e in ctx.store.events(ref=ctx.edit_id, kinds=[kind])]


async def test_a_diverging_control_replays_the_parent_and_holds_the_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, runner, golden = _setup(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("regression", calls))
    assert await handle_controlling(ctx) == "deciding"
    (spec,) = runner.submits
    assert spec.batch_id.startswith("ctrl_") and spec.candidate_commit == ctx.edit.parent
    assert spec.golden_run_ids == [golden.run_id]
    (final,) = load_pairs(ctx)
    assert final.outcome == "pass" and final.decided_by == "control"
    assert final.control_run_id == f"ctl_{golden.run_id}" and final.control_outcome == "diverges"
    assert control_path(ctx, golden.run_id).is_file()
    (event,) = _kinds(ctx, "pair.control")
    assert {k: event[k] for k in ("golden", "replay", "control", "control_outcome")} == {
        "golden": golden.run_id,
        "replay": final.replay_run_id,
        "control": f"ctl_{golden.run_id}",
        "control_outcome": "diverges",
    }
    assert _kinds(ctx, "pair.decided")[-1]["outcome"] == "pass"
    assert any(str(e["batch_id"]).startswith("ctrl_") for e in _kinds(ctx, "batch.submitted"))


async def test_an_agreeing_control_keeps_the_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, _runner, _golden = _setup(tmp_path)
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("pass", []))
    await handle_controlling(ctx)
    (final,) = load_pairs(ctx)
    assert final.outcome == "regression" and final.control_outcome == "agrees"
    assert not _kinds(ctx, "pair.decided")


async def test_a_restart_never_dispatches_or_judges_a_finished_control_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, runner, golden = _setup(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("regression", calls))
    await handle_controlling(ctx)
    await handle_controlling(ctx)  # everything saved: a no-op
    assert len(runner.submits) == 1 and len(calls) == 1
    assert len(_kinds(ctx, "pair.control")) == 1
    # Crash after the batch finished but before the control was judged: re-judge it from
    # the finished batch, never replay it again.
    control_path(ctx, golden.run_id).unlink()
    pair_path(ctx, golden.run_id, ".control").unlink()
    await handle_controlling(ctx)
    assert len(runner.submits) == 1 and len(calls) == 2
    assert load_pairs(ctx)[0].decided_by == "control"


@pytest.mark.parametrize(
    ("outcome", "decided_by", "settings"),
    [
        ("regression", "level0", {}),  # Level 0 decided: never a control
        ("pass", "nano", {}),  # nothing contested: no cost
        ("regression", "ultra", {"ratchet_feature_control_replays": False}),  # feature off
    ],
)
async def test_no_control_is_replayed_when_none_is_wanted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    decided_by: str,
    settings: dict[str, object],
) -> None:
    ctx, runner, golden = _setup(tmp_path, outcome, decided_by, **settings)
    calls: list[str] = []
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("regression", calls))
    assert await handle_controlling(ctx) == "deciding"
    assert not runner.submits and not calls
    assert not (ctx.work / "controls").exists()
    assert load_pairs(ctx)[0].outcome == outcome


async def test_reuse_controls_uses_copied_results_and_never_dispatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx, runner, golden = _setup(tmp_path)
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("regression", []))
    await handle_controlling(ctx)
    saved = ctx.work / "controls"
    copied = tmp_path / "copied"
    shutil.copytree(saved, copied)
    shutil.rmtree(saved)
    pair_path(ctx, golden.run_id, ".control").unlink()
    reuse = ctx.options.model_copy(update={"reuse_controls": True})
    rejudge = EditContext(ctx.services, ctx.edit, reuse, ctx.repo, ctx.client, ctx.tavily)
    await handle_controlling(rejudge)  # nothing copied: the pair stays as judged
    assert load_pairs(rejudge)[0].outcome == "regression" and len(runner.submits) == 1
    shutil.copytree(copied, saved)
    await handle_controlling(rejudge)
    assert load_pairs(rejudge)[0].decided_by == "control" and len(runner.submits) == 1


# --- the progress strip -----------------------------------------------------------------------
def test_the_controls_stage_counts_control_batches_separately(tmp_path: Path) -> None:
    ctx, _runner, _golden = _setup(tmp_path)
    store, edit_id = ctx.store, ctx.edit_id

    def emit(kind: str, at_s: float, **payload: object) -> None:
        with clock.frozen(T0 + timedelta(seconds=at_s)):
            store.emit(kind, edit_id, {"edit_id": edit_id, **payload})

    emit("batch.finished", 1, batch_id="batch_x_a1_00", runner="local-process", k=4, n=4)
    emit("batch.submitted", 2, batch_id="ctrl_x_a1_00", runner="local-process", k=0, n=2)
    emit("batch.heartbeat", 3, batch_id="ctrl_x_a1_00", runner="local-process", k=1, n=2)
    with clock.frozen(T0 + timedelta(seconds=3)):
        store.set_edit_state(edit_id, "controlling")
        progress = progress_for(store, edit_id)
    assert progress is not None and progress.stage == "controls"
    assert progress.stage_label == "Replaying controls (1/2)"
    assert (progress.k, progress.n) == (4, 4)  # the edit's replays only
    keys = [s.key for s in progress.stages]
    assert keys.index("drift") + 1 == keys.index("controls") == keys.index("summary") - 1


def test_the_controls_stage_is_skipped_when_nothing_was_contested(tmp_path: Path) -> None:
    ctx, _runner, _golden = _setup(tmp_path)
    ctx.store.set_edit_state(ctx.edit_id, "deciding")
    progress = progress_for(ctx.store, ctx.edit_id)
    assert progress is not None
    assert {s.key: s.status for s in progress.stages}["controls"] == "skipped"
    assert copy.stage_label("controls") == "Replaying controls"
    assert copy.stage_label("controls", 2, 3) == "Replaying controls (2/3)"


async def test_a_pair_the_drift_stage_settled_is_never_contested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drift runs before controls: a `drift` pair is settled, so no control is replayed."""
    ctx, runner, golden = _setup(tmp_path, "uncertain")
    judged = load_pairs(ctx)[0]
    drifted = judged.model_copy(update={"outcome": "drift", "decided_by": "drift"})
    write_json_atomic(pair_path(ctx, golden.run_id, ".drift"), drifted)
    calls: list[str] = []
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("regression", calls))
    assert await handle_controlling(ctx) == "deciding"
    assert not runner.submits and not calls
    assert load_pairs(ctx)[0].outcome == "drift"


async def test_the_control_layer_wins_over_a_drift_result_that_settled_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pair the drift check looked at but could not explain stays contested; its control
    result is the final layer."""
    ctx, runner, golden = _setup(tmp_path, "uncertain")
    judged = load_pairs(ctx)[0]
    write_json_atomic(pair_path(ctx, golden.run_id, ".drift"), judged)  # checked, unchanged
    calls: list[str] = []
    monkeypatch.setattr(controls, "judge_pair", _fake_judge("regression", calls))
    assert await handle_controlling(ctx) == "deciding"
    assert len(runner.submits) == 1
    (final,) = load_pairs(ctx)
    assert final.outcome == "pass" and final.decided_by == "control"
    assert final.control_outcome == "diverges" and final.error_code is None
