"""Shared builders for the orchestrator unit tests (no subprocesses, no network)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ratchet.config.settings import Settings
from ratchet.models import Level0Finding, PairVerdict, SkillEdit, Trace, Verdict
from ratchet.store import Store
from ratchet.watcher.edits import build_edit
from ratchet.watcher.gitops import SkillsRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = REPO_ROOT / "scenarios"
TRACES = REPO_ROOT / "tests" / "fixtures" / "traces"
T0 = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def make_settings(root: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "ratchet_data_dir": root / "data",
        "ratchet_skills_repo": root / "skills",
        "ratchet_upstream": "sim",
        "ratchet_scenarios_dir": SCENARIOS,
        "ratchet_tavily_fixtures_dir": REPO_ROOT / "tests" / "fixtures" / "tavily",
        "ratchet_log_level": "ERROR",
        "ratchet_public_mode": False,
        "ratchet_simulate_enabled": True,  # the default is off
        "ratchet_notify_webhook": None,
        "nebius_token_factory_key": None,
        "tavily_api_key": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def open_store(settings: Settings) -> Store:
    return Store.open(settings.ratchet_data_dir)


def seeded_repo(settings: Settings) -> SkillsRepo:
    repo = SkillsRepo(settings.ratchet_skills_repo)
    repo.init(SCENARIOS / "skills")
    return repo


def fixture_trace(name: str, which: str = "golden", **update: object) -> Trace:
    data = json.loads((TRACES / f"{name}.json").read_text(encoding="utf-8"))
    return Trace.model_validate(data[which]).model_copy(update=update)


def commit_edit(
    repo: SkillsRepo,
    files: dict[str, str | None],
    *,
    session_hash: str | None = None,
    stacked: bool = False,
) -> tuple[SkillEdit, str]:
    parent = repo.candidate_head() if stacked else None
    head = repo.apply_files(files, message="test edit", author="user")
    return build_edit(repo, commit=head, parent=parent, author="user", session_hash=session_hash)


def scenario_edit(repo: SkillsRepo, scenario_id: str) -> tuple[SkillEdit, str]:
    from ratchet.orchestrator.scenarios import get_scenario

    return commit_edit(repo, get_scenario(SCENARIOS, scenario_id).edit_files())


def empty_finding(golden: str, replay: str) -> Level0Finding:
    return Level0Finding(
        golden_run_id=golden,
        replay_run_id=replay,
        new_calls=[],
        missing_calls=[],
        changed_args=[],
        hard_fail=False,
    )


def pair(golden: str = "run_g", replay: str = "run_r", outcome: str = "pass") -> PairVerdict:
    return PairVerdict.model_validate(
        {
            "golden_run_id": golden,
            "replay_run_id": replay,
            "level0": empty_finding(golden, replay).model_dump(),
            "opinions": [],
            "drift": [],
            "outcome": outcome,
            "usd": 0.0,
        }
    )


def make_verdict(edit: SkillEdit, **update: object) -> Verdict:
    from ratchet.orchestrator.ids import verdict_id_for

    base = Verdict(
        verdict_id=verdict_id_for(edit.edit_id),
        edit_id=edit.edit_id,
        pairs=[pair()],
        decision="WARN",
        action="held",
        narrative="Held: judges disagreed on 1 of 1 replays. Review before merging.",
        cost={"nano": 0.001, "super": 0.0, "ultra": 0.0},
        duration_s=3,
        created_at=T0,
        headline="Held: judges disagreed on 1 of 1 replays. Review before merging.",
        session_hash=edit.session_hash,
        affected_skills=list(edit.affected_skills),
    )
    return base.model_copy(update=update)
