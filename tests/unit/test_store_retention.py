"""``ratchet purge --before``: old traces go, then cassettes recorded before the date that no
remaining trace references."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ratchet.cli.main import app
from ratchet.config.settings import reset_settings
from ratchet.models import Cassette, EnvSnapshot, ToolCall, Trace
from ratchet.store import Store
from ratchet.store.retention import referenced_cassettes

OLD = datetime(2026, 9, 1, tzinfo=UTC)
NEW = datetime(2026, 10, 15, tzinfo=UTC)
CUT = datetime(2026, 10, 1, tzinfo=UTC)


def _key(char: str) -> str:
    return char * 64


def _trace(run_id: str, kind: str, keys: list[str], created: datetime) -> Trace:
    steps = [
        ToolCall(
            step_id=f"t{i + 1:03d}",
            tool="github.list_issues",
            args_norm={},
            args_hash=key,
            result_hash=None,
            side_effect="read",
            source="cassette",
            cassette_key=key,
        )
        for i, key in enumerate(keys)
    ]
    return Trace.model_validate(
        {
            "run_id": run_id,
            "kind": kind,
            "task_input": "t",
            "steps": [s.model_dump() for s in steps],
            "final_output": "",
            "artifacts_ref": None,
            "skills_invoked": [],
            "world_dependent": False,
            "world_facts": [],
            "env": EnvSnapshot(
                workspace_ref="", frozen_time=OLD, harness_version="minimal/0", skills_commit="x"
            ),
            "agent_tier": "super",
            "seed": 0,
            "created_at": created,
            "golden_of": None,
            "candidate_commit": None,
        }
    )


def _cassette(store: Store, key: str, recorded: datetime) -> Path:
    cassette = Cassette(
        tool="github.list_issues",
        args_norm={},
        response={"ok": True},
        recorded_at=recorded,
        side_effect="read",
    )
    return store.save_cassette(key, cassette)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store.open(tmp_path / "data")


def test_purge_deletes_old_unreferenced_cassettes_only(store: Store) -> None:
    store.save_trace(_trace("run_old_live", "live", [_key("a")], OLD))
    store.save_trace(_trace("run_golden", "golden", [_key("b")], OLD))
    paths = {
        "a": _cassette(store, _key("a"), OLD),  # only the purged live run used it
        "b": _cassette(store, _key("b"), OLD),  # a remaining golden uses it
        "c": _cassette(store, _key("c"), OLD),  # nobody uses it
        "d": _cassette(store, _key("d"), NEW),  # unreferenced but recorded after the cut
    }
    assert store.purge(CUT) == 1
    assert store.purge_cassettes(CUT) == 2
    assert {k for k, p in paths.items() if p.exists()} == {"b", "d"}
    assert referenced_cassettes(store.data_dir / "traces") == {("github.list_issues", _key("b"))}


def test_in_flight_steps_keep_their_cassettes(store: Store) -> None:
    """A captured run without trace.json still references what its steps.tool.jsonl names."""
    pending = _trace("run_pending", "live", [_key("e")], OLD).steps[0]
    assert isinstance(pending, ToolCall)
    store.append_step("run_pending", pending)
    path = _cassette(store, _key("e"), OLD)
    assert store.purge_cassettes(CUT) == 0 and path.exists()


def test_an_unreadable_trace_keeps_every_cassette(store: Store) -> None:
    path = _cassette(store, _key("f"), OLD)
    broken = store.trace_dir("run_broken")
    broken.mkdir(parents=True)
    (broken / "trace.json").write_text("{not json", encoding="utf-8")
    assert store.purge_cassettes(CUT) == 0 and path.exists()


@pytest.fixture
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv("RATCHET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RATCHET_LOG_LEVEL", "ERROR")
    reset_settings()
    yield tmp_path / "data"
    reset_settings()


def test_purge_command_reports_traces_and_cassettes(cli_env: Path) -> None:
    store = Store.open(cli_env)
    store.save_trace(_trace("run_old_live", "live", [_key("a")], OLD))
    _cassette(store, _key("a"), OLD)
    result = CliRunner().invoke(app, ["purge", "--before", "2026-10-01"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "Purged 1 traces and 1 unreferenced cassettes."
