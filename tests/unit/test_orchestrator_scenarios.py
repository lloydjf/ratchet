"""The scenario catalog (scenarios/<k>/scenario.yaml + edit/)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import SCENARIOS  # noqa: E402

from ratchet.errors import ConfigError, NotFoundError  # noqa: E402
from ratchet.orchestrator.scenarios import get_scenario, load_scenarios  # noqa: E402


def test_the_three_demo_scenarios_load_with_their_expected_decisions() -> None:
    found = load_scenarios(SCENARIOS)
    assert list(found) == ["1", "2", "3"]
    assert [s.info.expected_decision for s in found.values()] == ["BLOCK", "BLOCK", "PASS"]
    assert found["1"].info.skill == "github-triage" and found["1"].summary
    assert found["3"].world_dir is not None and found["3"].tavily_dir is not None
    assert found["1"].world_dir is None


def test_edit_files_are_relative_to_the_skills_repo() -> None:
    files = get_scenario(SCENARIOS, "2").edit_files()
    assert list(files) == ["expense-report/SKILL.md"]
    assert "skip" in str(files["expense-report/SKILL.md"]).lower()


def test_an_unknown_scenario_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        get_scenario(SCENARIOS, "9")


def test_a_bad_expected_decision_is_a_config_error(tmp_path: Path) -> None:
    (tmp_path / "7").mkdir()
    (tmp_path / "7" / "scenario.yaml").write_text("scenario_id: 7\nexpected_decision: MAYBE\n")
    with pytest.raises(ConfigError):
        load_scenarios(tmp_path)


def test_reserved_and_manifestless_dirs_are_skipped(tmp_path: Path) -> None:
    for name in ("skills", "golden", "8"):
        (tmp_path / name).mkdir()
    (tmp_path / "skills" / "scenario.yaml").write_text("expected_decision: PASS\n")
    assert load_scenarios(tmp_path) == {}
