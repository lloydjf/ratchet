"""CLI output hygiene and operator-facing copy: JSON logs on stderr for CLI commands
(``serve`` keeps stdout), the all-time bench cap line, the budget and 503 not_ready texts."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from ratchet import copy
from ratchet.cli.bench import bench_spend_line
from ratchet.cli.main import app
from ratchet.config.settings import Settings, reset_settings
from ratchet.dashboard_api.errors import NotReady
from ratchet.log import get_logger, log_stream_name, set_log_stream
from ratchet.store import CostRow, Store


@pytest.fixture(autouse=True)
def _restore_stream() -> Iterator[None]:
    yield
    set_log_stream("stdout")
    reset_settings()


def test_serve_keeps_stdout_and_other_commands_log_to_stderr() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["serve", "--help"]).exit_code == 0
    assert log_stream_name() == "stdout"
    assert runner.invoke(app, ["doctor", "--help"]).exit_code == 0
    assert log_stream_name() == "stderr"


def test_logger_follows_the_selected_stream(capsys: pytest.CaptureFixture[str]) -> None:
    set_log_stream("stderr")
    get_logger("test").error("cli.hygiene.check", n=1)
    out, err = capsys.readouterr()
    assert out == "" and '"event":"cli.hygiene.check"' in err
    set_log_stream("stdout")
    get_logger("test").error("cli.hygiene.check", n=2)
    out, err = capsys.readouterr()
    assert '"n":2' in out and err == ""


def test_bench_spend_line_reports_all_time_bench_spend(tmp_path: Path) -> None:
    store = Store.open(tmp_path / "data")
    for i, usd in enumerate((1.25, 0.5)):
        store.record_cost(
            CostRow(
                call_id=f"run_b:{i}",
                run_id="run_b",
                edit_id=None,
                tier="super",
                model="m",
                prompt_tokens=1,
                completion_tokens=1,
                usd=usd,
                purpose="bench",
                cached=False,
                session_hash=None,
            )
        )
    settings = Settings(_env_file=None, ratchet_bench_usd_cap=12.0)  # type: ignore[call-arg]
    env = SimpleNamespace(store=store, settings=settings)
    assert bench_spend_line(env) == "bench spend so far (all-time): $1.75 of $12.00"  # type: ignore[arg-type]
    description = Settings.model_fields["ratchet_bench_usd_cap"].description or ""
    assert "all-time" in description


def test_budget_and_not_ready_copy() -> None:
    assert copy.ERROR_COPY["budget_exceeded"] == (
        "Spending cap reached for today. The edit is held; raise RATCHET_DAILY_USD_CAP in .env "
        "or wait until tomorrow."
    )
    assert copy.ERROR_COPY["not_ready"].startswith("Ratchet is still acting on this verdict")
    err = NotReady("Simulate is not available right now.")
    assert (
        err.message
        == copy.NOT_READY_HOST
        == ("This host is not fully started yet; try again in a moment.")
    )
    assert err.code == "not_ready" and err.http_status == 503
    assert err.context == {"reason": "Simulate is not available right now."}
    assert NotReady(context={"module": "digest"}).context == {"module": "digest"}
