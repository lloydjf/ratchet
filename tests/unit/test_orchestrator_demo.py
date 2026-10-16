"""Demo seeding helpers (the full seed runs in the e2e template fixture)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from orch_fakes import SCENARIOS, make_settings, open_store  # noqa: E402

from ratchet.errors import ConfigError  # noqa: E402
from ratchet.orchestrator.demo import estimate_usd, load_tasks, next_demo_task, task_seed  # noqa: E402


def test_the_demo_task_list_has_25_tasks_and_3_canaries() -> None:
    tasks = load_tasks(SCENARIOS / "golden" / "tasks.yaml")
    assert len(tasks) == 25 and sum(t.canary for t in tasks) == 3
    assert len({t.task_id for t in tasks}) == 25 and all(t.label for t in tasks)


def test_seeds_are_stable_per_task() -> None:
    assert task_seed("triage-widgets") == task_seed("triage-widgets")
    assert task_seed("triage-widgets") != task_seed("canary-percent")


def test_recording_25_goldens_costs_about_ten_cents_on_super() -> None:
    assert 0.03 < estimate_usd(25) < 0.2


def test_a_task_without_text_is_a_config_error(tmp_path: Path) -> None:
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks:\n  - task_id: x\n")
    with pytest.raises(ConfigError):
        load_tasks(path)


def test_agent_start_demo_walks_the_tasks_round_robin(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = open_store(settings)
    first, second = next_demo_task(settings, store), next_demo_task(settings, store)
    assert first.task_id != second.task_id
    for _ in range(23):
        next_demo_task(settings, store)
    assert next_demo_task(settings, store).task_id == first.task_id
