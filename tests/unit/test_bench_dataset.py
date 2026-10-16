"""The committed bench dataset is complete and valid (skills, tasks, world, configs)."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest

from ratchet.bench.dataset import BenchPaths, load_config, load_tasks, validate_dataset
from ratchet.mocktools.world import World
from ratchet.sim.agent import POLICIES
from ratchet.tools.catalog import get_tool

PATHS = BenchPaths(Path(__file__).resolve().parents[2] / "bench")
BENCH_SKILLS = {
    "expense-report",
    "github-triage",
    "calendar-scheduler",
    "changelog-writer",
    "csv-cleaner",
    "invoice-parser",
    "weekly-research-brief",
    "fx-expense-converter",
    "dependency-updater",
    "meeting-notes",
}
WORLD_DEPENDENT = {"weekly-research-brief", "fx-expense-converter", "dependency-updater"}


def test_dataset_validates_skills_world_workspaces_and_routing() -> None:
    assert validate_dataset(PATHS) == []


def test_ten_bench_skills_with_ten_tasks_and_two_edge_cases_each() -> None:
    skills = {p.name for p in PATHS.skills.iterdir() if p.is_dir()}
    assert skills == BENCH_SKILLS
    tasks = load_tasks(PATHS.tasks)
    per_skill = Counter(t.expected_skill for t in tasks if not t.canary)
    assert per_skill == dict.fromkeys(BENCH_SKILLS, 10)
    edges = Counter(t.expected_skill for t in tasks if t.edge)
    assert edges == dict.fromkeys(BENCH_SKILLS, 2)
    assert sum(1 for t in tasks if t.canary) == 3
    assert all(t.expected_skill is None for t in tasks if t.canary)


def test_skills_name_only_catalog_tools_and_world_dependence_matches_the_dataset() -> None:
    tool = re.compile(r"`([a-z]+\.[a-z_]+)`")
    for skill in BENCH_SKILLS:
        text = (PATHS.skills / skill / "SKILL.md").read_text(encoding="utf-8")
        names = {n for n in tool.findall(text) if get_tool(n) is not None}
        assert names, skill
        world = any(get_tool(n).world_dependent for n in names)  # type: ignore[union-attr]
        assert world == (skill in WORLD_DEPENDENT), skill


def test_smoke_subset_only_uses_skills_with_simulator_policies() -> None:
    smoke = [t for t in load_tasks(PATHS.tasks) if t.smoke]
    skills = {t.expected_skill for t in smoke if t.expected_skill}
    assert skills <= set(POLICIES) and len(skills) == 5
    assert sum(1 for t in smoke if t.canary) == 3


def test_world_is_synthetic_and_frozen() -> None:
    world = World.load(PATHS.world)
    assert world.frozen_time is not None and world.frozen_time.isoformat().startswith("2026-10-14")
    for path in PATHS.world.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        for url in re.findall(r"https?://([a-z0-9.-]+)", text):
            assert url.endswith("example.com"), (path.name, url)


@pytest.mark.parametrize("path", sorted(PATHS.configs.glob("*.yaml")), ids=lambda p: p.stem)
def test_configs_load_and_ablations_reuse_the_cascade_replays(path: Path) -> None:
    config = load_config(path)
    assert config.name == path.stem
    if config.name not in ("cascade", "noise"):
        assert config.reuse_replays == "cascade"
    if config.cascade.mode != "cascade":
        assert config.blocks_on_single_judge


def test_no_dataset_file_sits_in_a_gitignored_directory_name() -> None:
    ignored = {"data", "build", "dist", "var", "__pycache__", "node_modules"}
    for path in PATHS.root.rglob("*"):
        if path.is_file() and "results" not in path.relative_to(PATHS.root).parts:
            assert not ignored & set(path.relative_to(PATHS.root).parts[:-1]), path
