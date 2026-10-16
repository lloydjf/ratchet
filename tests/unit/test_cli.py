"""CLI smoke tests with typer.testing.CliRunner (design §10). No network: sim upstream."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import REPO_ROOT, SCENARIOS  # noqa: E402

from ratchet.cli.main import app  # noqa: E402
from ratchet.cli.schemas import model_schemas  # noqa: E402
from ratchet.config.settings import reset_settings  # noqa: E402

runner = CliRunner()


@pytest.fixture(autouse=True)
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("RATCHET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RATCHET_SKILLS_REPO", str(tmp_path / "skills"))
    monkeypatch.setenv("RATCHET_SCENARIOS_DIR", str(SCENARIOS))
    monkeypatch.setenv("RATCHET_LOG_LEVEL", "ERROR")
    reset_settings()
    yield tmp_path
    reset_settings()


def test_doctor_prints_a_table_and_passes_on_a_dev_box() -> None:
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    for name in ("settings", "models", "runner", "sandbox", "tavily", "git", "disk", "caps"):
        assert name in result.output
    assert "offline simulator" in result.output


def test_doctor_fails_public_mode_with_the_process_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATCHET_PUBLIC_MODE", "true")
    reset_settings()
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1 and "refused in public mode" in result.output


def test_db_migrate_reports_the_schema_version() -> None:
    result = runner.invoke(app, ["db", "migrate"])
    assert result.exit_code == 0 and "Schema version 4." in result.output


def test_committed_model_schemas_match_the_models() -> None:
    """``ratchet schemas export`` output is committed under schemas/."""
    for name, text in model_schemas().items():
        committed = REPO_ROOT / "schemas" / name
        assert committed.read_text(encoding="utf-8") == text, f"{name} is stale"


def test_schemas_export_writes_every_model(tmp_path: Path) -> None:
    result = runner.invoke(app, ["schemas", "export", "--out", str(tmp_path / "out")])
    assert result.exit_code == 0
    assert (tmp_path / "out" / "Verdict.json").is_file()
    assert json.loads((tmp_path / "out" / "Trace.json").read_text())["title"] == "Trace"


def test_api_export_openapi_prints_the_dashboard_document() -> None:
    from ratchet.dashboard_api.openapi import render_openapi

    result = runner.invoke(app, ["api", "export-openapi"])
    assert result.exit_code == 0 and result.output == render_openapi()


def test_status_and_metrics_work_on_an_empty_store() -> None:
    status = runner.invoke(app, ["status"])
    assert status.exit_code == 0 and "Edits in flight" in status.output
    metrics = runner.invoke(app, ["metrics", "--since", "7d"])
    assert metrics.exit_code == 0 and "Blessed traces" in metrics.output
    funnel = runner.invoke(app, ["metrics", "--demo"])
    assert funnel.exit_code == 0 and "demo.landing_view" in funnel.output
    assert runner.invoke(app, ["metrics", "--since", "soon"]).exit_code == 2


def test_verify_live_needs_a_key_and_spends_nothing_without_one() -> None:
    result = runner.invoke(app, ["verify-live", "--yes"])
    assert result.exit_code == 1 and "nothing was called" in result.output


def test_verify_live_prints_the_cost_and_asks_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEBIUS_TOKEN_FACTORY_KEY", "test-key-not-real-000")
    reset_settings()
    calls: list[object] = []
    monkeypatch.setattr("ratchet.cli.verify_live.run_checks", lambda *a: calls.append(a))
    result = runner.invoke(app, ["verify-live"], input="n\n")
    assert result.exit_code == 1 and "$0.05" in result.output and "Continue?" in result.output
    assert calls == []


def test_errors_exit_non_zero_with_their_code() -> None:
    unknown = runner.invoke(app, ["simulate", "9", "--headless"])
    assert unknown.exit_code == 1 and "not_found" in unknown.output
    missing = runner.invoke(app, ["rollback", "verdict_nope"])
    assert missing.exit_code == 1 and "not_found" in missing.output
    assert runner.invoke(app, ["bless"]).exit_code == 1
    assert runner.invoke(app, ["purge", "--before", "yesterday"]).exit_code == 2
    assert runner.invoke(app, ["agent", "start"]).exit_code == 1
