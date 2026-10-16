"""The scenario catalog: ``scenarios/<k>/scenario.yaml`` + ``edit/`` (+ ``world/``, ``tavily/``).

``scenario.yaml`` keys (design: scenarios-and-simulator §3): ``id``, ``skill``, ``title``,
``learned``, ``catches``, ``expected_decision``, an optional edit ``summary`` and an
optional ``live_world`` block (``ratchet.orchestrator.live_world``). ``edit/`` holds the
full new contents of each changed file, relative to the skills repo root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel

from ratchet.errors import ConfigError, NotFoundError
from ratchet.models import Decision
from ratchet.orchestrator.live_world import LiveWorldSpec

__all__ = ["ScenarioInfo", "Scenario", "load_scenarios", "get_scenario", "RESERVED_DIRS"]

RESERVED_DIRS = frozenset({"skills", "golden", "world", "recorded"})


class ScenarioInfo(BaseModel):
    scenario_id: str
    skill: str
    title: str
    learned: str
    catches: str
    expected_decision: Decision


@dataclass(frozen=True)
class Scenario:
    info: ScenarioInfo
    root: Path
    summary: str
    live_world: LiveWorldSpec | None = None  # track a real-world value on live Tavily

    @property
    def scenario_id(self) -> str:
        return self.info.scenario_id

    @property
    def world_dir(self) -> Path | None:
        path = self.root / "world"
        return path if path.is_dir() else None

    @property
    def tavily_dir(self) -> Path | None:
        path = self.root / "tavily"
        return path if path.is_dir() else None

    def edit_files(self) -> dict[str, str | None]:
        edit = self.root / "edit"
        if not edit.is_dir():
            raise ConfigError(f"scenario {self.scenario_id} has no edit/ directory")
        return {
            p.relative_to(edit).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(edit.rglob("*"))
            if p.is_file() and not p.name.startswith(".")
        }


def _text(raw: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is not None:
            return str(value).strip()
    return ""


def _parse(path: Path) -> Scenario:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} is not a mapping", code="config_error")
    decision = _text(raw, "expected_decision", "expected").upper()
    if decision not in ("PASS", "WARN", "BLOCK"):
        raise ConfigError(f"{path}: expected_decision must be PASS, WARN or BLOCK")
    info = ScenarioInfo(
        scenario_id=_text(raw, "id", "scenario_id") or path.parent.name,
        skill=_text(raw, "skill"),
        title=_text(raw, "title"),
        learned=_text(raw, "learned"),
        catches=_text(raw, "catches"),
        expected_decision="PASS"
        if decision == "PASS"
        else ("WARN" if decision == "WARN" else "BLOCK"),
    )
    summary = raw.get("edit")
    if isinstance(summary, dict):
        summary = summary.get("summary")
    text = str(summary or _text(raw, "summary", "edit_summary")).strip()
    live = raw.get("live_world")
    spec = LiveWorldSpec.model_validate(live) if isinstance(live, dict) else None
    return Scenario(info=info, root=path.parent, summary=text, live_world=spec)


def load_scenarios(root: Path) -> dict[str, Scenario]:
    """Scenarios under ``root`` keyed by id, in directory order."""
    found: dict[str, Scenario] = {}
    if not root.is_dir():
        return found
    for child in sorted(root.iterdir(), key=lambda p: (len(p.name), p.name)):
        manifest = child / "scenario.yaml"
        if child.name in RESERVED_DIRS or not manifest.is_file():
            continue
        scenario = _parse(manifest)
        found[scenario.scenario_id] = scenario
    return found


def get_scenario(root: Path, scenario_id: str) -> Scenario:
    scenario = load_scenarios(root).get(scenario_id)
    if scenario is None:
        raise NotFoundError(f"unknown scenario {scenario_id}", code="not_found")
    return scenario
