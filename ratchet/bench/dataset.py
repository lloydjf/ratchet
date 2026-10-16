"""The bench dataset on disk (``bench/``): paths, task inputs, configs, and validation.

```
bench/
  skills/<name>/SKILL.md      the 10 bench skills (agentskills.io)
  world/                      the bench world (ratchet/mocktools/WORLD_FORMAT.md)
  workspaces/<name>/          initial workspaces copied in for a task
  tasks.yaml                  100 task inputs + 3 canaries
  configs/<name>.yaml         sweep configurations (E1–E6)
  edits/<edit_id>/            one labeled edit: edit.yaml + files/ (+ world/, tavily/ for D1)
  labels/labels.json          the labels (step 4)
  tavily_fixtures/            recorded Tavily responses for unchanged facts
  results/<date>/             results.json, report.md, cost_vs_recall.svg
```
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ratchet.bench.taxonomy import ALL_TYPES, EditType
from ratchet.errors import ConfigError
from ratchet.judge.cascade import CascadeConfig

__all__ = [
    "BenchPaths",
    "BenchTask",
    "CascadeSpec",
    "BenchConfig",
    "load_tasks",
    "load_config",
    "validate_dataset",
    "DEFAULT_ROOT",
]

DEFAULT_ROOT = Path("bench")


@dataclass(frozen=True)
class BenchPaths:
    root: Path = DEFAULT_ROOT

    @property
    def skills(self) -> Path:
        return self.root / "skills"

    @property
    def world(self) -> Path:
        return self.root / "world"

    @property
    def workspaces(self) -> Path:
        return self.root / "workspaces"

    @property
    def tasks(self) -> Path:
        return self.root / "tasks.yaml"

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def edits(self) -> Path:
        return self.root / "edits"

    @property
    def labels(self) -> Path:
        return self.root / "labels" / "labels.json"

    @property
    def tavily(self) -> Path:
        return self.root / "tavily_fixtures"

    @property
    def results(self) -> Path:
        return self.root / "results"


@dataclass(frozen=True)
class BenchTask:
    task_id: str
    task_input: str
    label: str
    expected_skill: str | None
    workspace: str | None = None
    edge: bool = False
    smoke: bool = False
    canary: bool = False
    extra: dict[str, object] = field(default_factory=dict)


def _str(item: dict[str, object], key: str) -> str | None:
    value = item.get(key)
    return str(value).strip() if value is not None and str(value).strip() else None


def load_tasks(path: Path) -> list[BenchTask]:
    if not path.is_file():
        raise ConfigError(f"bench task list {path} not found", code="config_error")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = raw.get("tasks", []) if isinstance(raw, dict) else raw
    tasks: list[BenchTask] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        task_id, text = _str(item, "task_id"), _str(item, "task_input")
        if task_id is None or text is None:
            raise ConfigError("every bench task needs task_id and task_input", code="config_error")
        tasks.append(
            BenchTask(
                task_id=task_id,
                task_input=text,
                label=_str(item, "task_label") or text[:60],
                expected_skill=_str(item, "expected_skill"),
                workspace=_str(item, "workspace"),
                edge=bool(item.get("edge", False)),
                smoke=bool(item.get("smoke", False)),
                canary=bool(item.get("canary", False)),
            )
        )
    ids = [t.task_id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ConfigError("duplicate task_id in the bench task list", code="config_error")
    return tasks


class CascadeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["cascade", "nano_only", "super_only", "ultra_only"] = "cascade"
    level0: bool = True
    drift: bool = True
    # ADR 0023 control replays; consumed by the orchestrator settings, not CascadeConfig.
    controls: bool = True

    def to_config(self) -> CascadeConfig:
        return CascadeConfig(mode=self.mode, level0=self.level0, drift=self.drift)


class BenchConfig(BaseModel):
    """``bench/configs/<name>.yaml``: one sweep configuration."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    experiments: list[str] = Field(default_factory=list)
    mode: Literal["edits", "noise"] = "edits"
    cascade: CascadeSpec = Field(default_factory=CascadeSpec)
    single_judge_blocks: bool | None = None  # default: true for the single-tier E4 modes
    types: list[EditType] = Field(default_factory=lambda: list(ALL_TYPES))
    noise_repeats: int = Field(3, ge=1)
    runner: Literal["local", "nebius"] = "local"
    concurrency: int = Field(2, ge=1)
    # Judge-only ablations re-judge the replays of another configuration's run in the same
    # results directory (paired comparison, no second round of agent spend).
    reuse_replays: str | None = None
    max_traces: int = Field(40, ge=1)  # selection cap per edit

    @property
    def blocks_on_single_judge(self) -> bool:
        if self.single_judge_blocks is not None:
            return self.single_judge_blocks
        return self.cascade.mode != "cascade"


def load_config(path: Path) -> BenchConfig:
    if not path.is_file():
        raise ConfigError(f"bench config {path} not found", code="config_error")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"bench config {path} is not a mapping", code="config_error")
    data.setdefault("name", path.stem)
    return BenchConfig.model_validate(data)


def validate_dataset(paths: BenchPaths, *, subset: list[BenchTask] | None = None) -> list[str]:
    """Problems with the dataset (empty = valid): skills, world, workspaces, trigger routing."""
    from ratchet.mocktools.world import World
    from ratchet.replay.harness.skills import load_skills as harness_skills
    from ratchet.replay.harness.skills import select_skills
    from ratchet.skills import parse_skill_md, validate_skill

    problems: list[str] = []
    for skill_dir in sorted(p for p in paths.skills.iterdir() if p.is_dir()):
        doc = parse_skill_md((skill_dir / "SKILL.md").read_text(encoding="utf-8"))
        problems += [f"{skill_dir.name}: {p}" for p in validate_skill(doc, dir_name=skill_dir.name)]
    try:
        World.load(paths.world)
    except ConfigError as exc:
        problems.append(f"world: {exc.message}")
    skills = harness_skills(paths.skills)
    for task in subset if subset is not None else load_tasks(paths.tasks):
        if task.workspace and not (paths.workspaces / task.workspace).is_dir():
            problems.append(f"{task.task_id}: workspace {task.workspace} missing")
        chosen = select_skills(task.task_input, skills)
        first = chosen[0].name if chosen else None
        if first != task.expected_skill:
            problems.append(f"{task.task_id}: routes to {first}, expected {task.expected_skill}")
    return problems
