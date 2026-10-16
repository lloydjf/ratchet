"""ADR 0023 second amendment: a Level 0 hard fail on a tool the parent skill already
instructs is contested (agent variance), and its control settles it."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import (  # noqa: E402
    commit_edit,
    empty_finding,
    fixture_trace,
    make_settings,
    open_store,
    seeded_repo,
)
from test_orchestrator_controls import FakeRunner  # noqa: E402

from ratchet import copy  # noqa: E402
from ratchet.judge.decide import decide  # noqa: E402
from ratchet.models import Level0Finding, PairVerdict, ToolCall, Trace  # noqa: E402
from ratchet.orchestrator.context import EditContext, Services, build_context  # noqa: E402
from ratchet.orchestrator.controls import (  # noqa: E402
    apply_control,
    control_outcome,
    control_path,
    handle_controlling,
    hard_contested,
    instructed,
)
from ratchet.orchestrator.embed import LocalHashEmbedder  # noqa: E402
from ratchet.orchestrator.judging import load_pairs, pair_path  # noqa: E402
from ratchet.store import SelectedTrace  # noqa: E402
from ratchet.store.base import write_json_atomic  # noqa: E402

FIXTURE = "r2_dropped_field"
COMMENT = "github.comment"
TRIAGE_TEXT = Path(__file__).resolve().parents[2] / "scenarios/skills/github-triage/SKILL.md"


def _call(tool: str, side_effect: str = "write_irreversible", step: str = "t900") -> ToolCall:
    return ToolCall.model_validate(
        {
            "step_id": step,
            "tool": tool,
            "args_norm": {"issue": 7},
            "args_hash": f"h-{tool}",
            "result_hash": None,
            "side_effect": side_effect,
            "source": "cassette",
            "cassette_key": None,
        }
    )


def _hard(golden: str, replay: str, *tools: str) -> Level0Finding:
    calls = [_call(t, step=f"t9{i:02d}") for i, t in enumerate(tools)]
    return empty_finding(golden, replay).model_copy(update={"new_calls": calls, "hard_fail": True})


def _pair(*tools: str, **update: Any) -> PairVerdict:
    base = PairVerdict(
        golden_run_id="run_g",
        replay_run_id="run_r",
        level0=_hard("run_g", "run_r", *tools),
        opinions=[],
        drift=[],
        outcome="regression",
        usd=0.0,
        decided_by="level0",
    )
    return base.model_copy(update=update)


def _control(finding: Level0Finding, outcome: str = "regression") -> PairVerdict:
    decided = "level0" if finding.hard_fail else "nano"
    return _pair().model_copy(
        update={
            "replay_run_id": "run_c",
            "level0": finding,
            "outcome": outcome,
            "decided_by": decided,
            "usd": 0.001,
        }
    )


# --- the rule (pure) --------------------------------------------------------------------------
def test_a_tool_is_instructed_by_its_dotted_or_wire_name_or_backticked_last_segment() -> None:
    text = TRIAGE_TEXT.read_text(encoding="utf-8")
    assert instructed(COMMENT, text) and instructed("github.label", text)
    assert instructed("GitHub.Comment", "call GITHUB.COMMENT once")  # case-insensitive
    assert instructed(COMMENT, "call github__comment with the body")  # wire name
    assert instructed(COMMENT, "then `comment` on the issue")  # fallback: backticked segment
    assert not instructed(COMMENT, "then comment on the issue")  # prose is not an instruction
    assert not instructed("github.close_issue", text)  # "Do not close ..." is not `close_issue`


def test_only_a_hard_fail_on_instructed_tools_is_contested() -> None:
    text = TRIAGE_TEXT.read_text(encoding="utf-8")
    assert hard_contested(_pair(COMMENT), [text])
    assert hard_contested(_pair(COMMENT), ["unrelated", text])  # any affected skill
    assert not hard_contested(_pair("github.close_issue"), [text])  # scenario 1: the edit's
    assert not hard_contested(_pair(COMMENT, "github.close_issue"), [text])  # every tool
    assert not hard_contested(_pair(COMMENT), [])  # no parent skill text: stands
    assert not hard_contested(_pair(COMMENT, decided_by="nano"), [text])
    assert not hard_contested(_pair(COMMENT, error_code="harness_failed"), [text])


def test_a_control_repeating_the_same_hard_call_turns_the_hard_fail_into_a_noisy_pass() -> None:
    pair = _pair(COMMENT)
    control = _control(_hard("run_g", "run_c", COMMENT))
    assert control_outcome(pair, control) == "diverges"
    adjusted = apply_control(pair, control)
    assert adjusted.outcome == "pass" and adjusted.decided_by == "control"
    assert adjusted.reason == copy.explain_nondeterminism()
    assert adjusted.level0 == pair.level0 and adjusted.level0.hard_fail  # kept as evidence
    result = decide([adjusted], n_failed=0, n_total=1, ultra_timed_out=False)
    assert result.decision == "PASS" and "level0_hard_fail" not in result.reasons


@pytest.mark.parametrize(
    "finding",
    [
        empty_finding("run_g", "run_c"),  # the unchanged skill skipped the comment
        _hard("run_g", "run_c", "gmail.send"),  # a different hard call is not the same
    ],
)
def test_a_control_without_the_same_hard_call_leaves_the_hard_fail_standing(
    finding: Level0Finding,
) -> None:
    pair = _pair(COMMENT)
    control = _control(finding, "pass" if not finding.hard_fail else "regression")
    assert control_outcome(pair, control) == "agrees"
    adjusted = apply_control(pair, control)
    assert adjusted.outcome == "regression" and adjusted.decided_by == "level0"
    result = decide([adjusted], n_failed=0, n_total=1, ultra_timed_out=False)
    assert result.decision == "BLOCK" and result.reasons[0] == "level0_hard_fail"


def test_a_failed_control_says_nothing_about_a_hard_fail() -> None:
    control = _control(empty_finding("run_g", ""), "failed").model_copy(
        update={"replay_run_id": ""}
    )
    assert control_outcome(_pair(COMMENT), control) is None


# --- the controlling state --------------------------------------------------------------------
class CommentingRunner(FakeRunner):
    """The control (a copy of the golden) also posts the comment the replay posted."""

    def __init__(self, store: Any, *, comment: bool) -> None:
        super().__init__(store)
        self.comment = comment

    async def fetch(self, job_id: str, output_dir: Path) -> Path:
        await super().fetch(job_id, output_dir)
        if not self.comment:
            return output_dir
        from ratchet.models import ReplayResult

        path = output_dir / "result.json"
        result = ReplayResult.model_validate_json(path.read_text(encoding="utf-8"))
        extra = [t.model_copy(update={"steps": [*t.steps, _call(COMMENT)]}) for t in result.replays]
        write_json_atomic(path, result.model_copy(update={"replays": extra}))
        return output_dir


def _setup(
    tmp_path: Path, tool: str, *, comment: bool
) -> tuple[EditContext, CommentingRunner, Trace]:
    settings = make_settings(tmp_path, ratchet_sandbox="docker")
    store = open_store(settings)
    runner = CommentingRunner(store, comment=comment)
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
    edit, diff = commit_edit(repo, {"github-triage/NOTE.md": "x\n"})
    store.save_edit(edit, diff)
    golden = fixture_trace(FIXTURE, "golden")
    replay = fixture_trace(FIXTURE, "replay")
    store.save_trace(golden)
    store.save_trace(replay)
    store.save_selection(edit.edit_id, [SelectedTrace(golden.run_id, "direct", 1.0)])
    ctx = build_context(services, edit.edit_id)
    judged = _pair(tool).model_copy(
        update={
            "golden_run_id": golden.run_id,
            "replay_run_id": replay.run_id,
            "level0": _hard(golden.run_id, replay.run_id, tool),
        }
    )
    write_json_atomic(pair_path(ctx, golden.run_id), judged)
    return ctx, runner, golden


async def test_a_hard_fail_the_parent_skill_instructs_gets_a_control_that_passes_it(
    tmp_path: Path,
) -> None:
    ctx, runner, golden = _setup(tmp_path, COMMENT, comment=True)
    assert await handle_controlling(ctx) == "deciding"
    (spec,) = runner.submits
    assert spec.batch_id.startswith("ctrl_") and spec.candidate_commit == ctx.edit.parent
    (final,) = load_pairs(ctx)
    assert final.outcome == "pass" and final.decided_by == "control"
    assert final.control_outcome == "diverges" and final.level0.hard_fail
    control = PairVerdict.model_validate_json(control_path(ctx, golden.run_id).read_text())
    assert control.decided_by == "level0" and not control.opinions  # no model needed


async def test_a_hard_fail_whose_control_skips_the_call_still_blocks(tmp_path: Path) -> None:
    ctx, runner, _golden = _setup(tmp_path, COMMENT, comment=False)
    await handle_controlling(ctx)
    assert len(runner.submits) == 1
    (final,) = load_pairs(ctx)
    assert final.outcome == "regression" and final.decided_by == "level0"
    assert final.control_outcome == "agrees"


async def test_a_hard_call_the_parent_skill_never_names_gets_no_control(tmp_path: Path) -> None:
    ctx, runner, _golden = _setup(tmp_path, "github.close_issue", comment=True)
    await handle_controlling(ctx)
    assert runner.submits == [] and not (ctx.work / "controls").exists()
    (final,) = load_pairs(ctx)
    assert final.outcome == "regression" and final.decided_by == "level0"
