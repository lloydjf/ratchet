"""`ratchet simulate` (runs whatever RATCHET_SIMULATE_ENABLED says; --repeat)
and the flag's default and deploy settings."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import REPO_ROOT, SCENARIOS, make_settings, make_verdict  # noqa: E402

from ratchet import clock  # noqa: E402
from ratchet.cli import simulate_cmd  # noqa: E402
from ratchet.cli.main import app  # noqa: E402
from ratchet.cli.simulate_cmd import RepeatOutcome, SimulateOutcome, cli_settings  # noqa: E402
from ratchet.config.settings import Settings, reset_settings  # noqa: E402
from ratchet.models import SkillEdit  # noqa: E402

runner = CliRunner()


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("RATCHET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RATCHET_SKILLS_REPO", str(tmp_path / "skills"))
    monkeypatch.setenv("RATCHET_SCENARIOS_DIR", str(SCENARIOS))
    monkeypatch.setenv("RATCHET_LOG_LEVEL", "ERROR")
    monkeypatch.setenv("RATCHET_SIMULATE_ENABLED", "false")
    reset_settings()
    yield tmp_path
    reset_settings()


def _outcome(decision: str, elapsed: float, expected: str = "BLOCK") -> SimulateOutcome:
    edit = SkillEdit(
        edit_id="edit_1",
        commit="c",
        parent="p",
        diff_ref="d",
        affected_skills=["github-triage"],
        files=["github-triage/SKILL.md"],
        summary="s",
        detected_at=clock.now(),
        author="seeded",
    )
    verdict = make_verdict(edit, decision=decision, action="merged", created_at=clock.now())
    return SimulateOutcome(verdict, expected, elapsed)


def test_simulate_is_off_by_default_and_on_in_every_demo_config(tmp_path: Path) -> None:
    assert Settings(_env_file=None).ratchet_simulate_enabled is False  # type: ignore[call-arg]
    pattern = re.compile(r"^\s*(export )?RATCHET_SIMULATE_ENABLED[=:]", re.M)
    for rel in ("deploy/vm/env.template", ".env.example", "deploy/control-entrypoint.sh"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if pattern.match(ln)]
        assert lines and "true" in lines[-1], rel
    compose = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "RATCHET_SIMULATE_ENABLED: ${RATCHET_SIMULATE_ENABLED:-true}" in compose


def test_cli_settings_turn_simulate_on_for_the_operator(tmp_path: Path) -> None:
    off = make_settings(tmp_path, ratchet_simulate_enabled=False)
    assert cli_settings(off).ratchet_simulate_enabled is True
    assert off.ratchet_simulate_enabled is False  # the original is untouched
    on = make_settings(tmp_path)
    assert cli_settings(on) is on


def test_repeat_outcome_reports_match_rate_mean_and_p95() -> None:
    runs = (_outcome("BLOCK", 10.0), _outcome("BLOCK", 20.0), _outcome("PASS", 30.0))
    result = RepeatOutcome("1", runs)
    assert result.n_matched == 2 and not result.all_matched
    assert result.mean_s == 20.0 and result.p95_s == 30.0
    assert RepeatOutcome("1", runs[:2]).all_matched


def test_repeat_flag_exits_zero_only_when_every_run_matched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, bool]] = []
    results = {"good": ("BLOCK", "BLOCK"), "bad": ("BLOCK", "PASS")}

    async def fake_repeat(
        settings: Settings, store: object, scenario: str, *, repeat: int, timeout_s: float
    ) -> RepeatOutcome:
        assert timeout_s == 300.0  # the --timeout default
        calls.append((scenario, repeat, settings.ratchet_simulate_enabled))
        decisions = results["good" if scenario == "1" else "bad"]
        return RepeatOutcome(scenario, tuple(_outcome(d, 12.0) for d in decisions))

    monkeypatch.setattr(simulate_cmd, "simulate_repeat", fake_repeat)
    good = runner.invoke(app, ["simulate", "1", "--repeat", "2"])
    bad = runner.invoke(app, ["simulate", "2", "--repeat", "2"])
    assert good.exit_code == 0, good.output
    assert "Scenario 1: 2/2 matched (expected BLOCK); mean 12.0s, p95 12.0s" in good.output
    assert bad.exit_code == 1 and "1/2 matched" in bad.output
    assert calls == [("1", 2, False), ("2", 2, False)]  # the CLI never needs the HTTP flag
    refused = runner.invoke(app, ["simulate", "1", "--repeat", "2", "--record-fixture"])
    assert refused.exit_code == 1
