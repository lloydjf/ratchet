"""Domain-object factories for the dashboard API tests (ratchet.models, stored via Store)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from ratchet.models import (
    Decision,
    EnvSnapshot,
    Level0Finding,
    PairOutcome,
    PairVerdict,
    SkillEdit,
    Trace,
    Verdict,
)
from ratchet.store import Store

__all__ = [
    "NOW",
    "DIFF",
    "later",
    "make_trace",
    "make_edit",
    "make_pair",
    "make_verdict",
    "seed_decided",
    "seed_running",
]

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
DIFF = "--- a/skills/github-triage/SKILL.md\n+++ b/skills/github-triage/SKILL.md\n@@\n+close\n"


# -- domain object factories -------------------------------------------------------------
def make_trace(
    run_id: str,
    kind: Literal["live", "golden", "replay"] = "golden",
    *,
    skills: tuple[str, ...] = ("github-triage",),
    label: str = "Triage acme/widgets backlog",
    created_at: datetime = NOW,
) -> Trace:
    return Trace(
        run_id=run_id,
        kind=kind,
        task_input=f"Please handle task {run_id}",
        steps=[],
        final_output="done",
        artifacts_ref=None,
        skills_invoked=list(skills),
        world_dependent=False,
        world_facts=[],
        env=EnvSnapshot(
            workspace_ref="workspace.tar.zst",
            frozen_time=NOW,
            harness_version="minimal/0.1.0",
            skills_commit="abc123",
        ),
        agent_tier="super",
        seed=7,
        created_at=created_at,
        golden_of=None,
        candidate_commit=None,
        task_label=label,
        blessed_at=NOW if kind == "golden" else None,
    )


def make_edit(edit_id: str, *, session_hash: str | None = None, at: datetime = NOW) -> SkillEdit:
    return SkillEdit(
        edit_id=edit_id,
        commit="c0ffee",
        parent="abc123",
        files=["skills/github-triage/SKILL.md"],
        diff_ref="",
        affected_skills=["github-triage"],
        author="seeded" if session_hash else "agent",
        detected_at=at,
        session_hash=session_hash,
    )


def make_pair(golden: str, replay: str, outcome: PairOutcome = "regression") -> PairVerdict:
    finding = Level0Finding(
        golden_run_id=golden,
        replay_run_id=replay,
        new_calls=[],
        missing_calls=[],
        changed_args=[],
        hard_fail=outcome == "regression",
    )
    return PairVerdict(
        golden_run_id=golden,
        replay_run_id=replay,
        level0=finding,
        opinions=[],
        drift=[],
        outcome=outcome,
        usd=0.001,
    )


def make_verdict(
    verdict_id: str,
    edit_id: str,
    *,
    session_hash: str | None = None,
    pairs: list[PairVerdict] | None = None,
    decision: Decision = "BLOCK",
    created_at: datetime = NOW,
) -> Verdict:
    return Verdict(
        verdict_id=verdict_id,
        edit_id=edit_id,
        pairs=pairs or [],
        decision=decision,
        action="reverted" if decision == "BLOCK" else "merged",
        narrative="Blocked: the new github-triage would close issues in 1 of 1 replays. More.",
        cost={"nano": 0.002, "super": 0.01, "ultra": 0.0},
        duration_s=42,
        created_at=created_at,
        headline="Blocked: the new github-triage would close issues in 1 of 1 replays.",
        session_hash=session_hash,
        affected_skills=["github-triage"],
        simulated_models=True,
    )


def seed_decided(
    store: Store,
    hexid: str,
    *,
    session_hash: str | None = None,
    at: datetime = NOW,
    decision: Decision = "BLOCK",
) -> Verdict:
    """A finished edit with one regression pair and its two traces."""
    edit = make_edit(f"edit_{hexid}", session_hash=session_hash, at=at)
    store.save_edit(edit, DIFF)
    golden, replay = f"run_g{hexid}", f"run_r{hexid}"
    store.save_trace(make_trace(golden, "golden", created_at=at))
    store.save_trace(make_trace(replay, "replay", created_at=at))
    verdict = make_verdict(
        f"verdict_{hexid}",
        edit.edit_id,
        session_hash=session_hash,
        pairs=[make_pair(golden, replay)],
        decision=decision,
        created_at=at,
    )
    store.save_verdict(verdict)
    store.set_edit_state(edit.edit_id, "done", verdict_id=verdict.verdict_id)
    store.emit(
        "verdict.decided", verdict.verdict_id, {"decision": decision}, session_hash=session_hash
    )
    return verdict


def seed_running(store: Store, hexid: str, *, session_hash: str | None = None) -> SkillEdit:
    edit = store.save_edit(make_edit(f"edit_{hexid}", session_hash=session_hash), DIFF)
    store.set_edit_state(edit.edit_id, "collecting")
    return edit


def later(minutes: int) -> datetime:
    return NOW + timedelta(minutes=minutes)
