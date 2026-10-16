"""The bench work area: its own Store and skills repo, separate from the operator's data.

``<work>/data`` is a full Ratchet store (goldens, cassettes, edits, verdicts, costs) and
``<work>/skills`` the bench skills repo (``main`` = the original ``bench/skills``). Every
labeled edit runs on a scratch ``clone_to`` copy, so the bench repo's ``main`` never moves.
Because the store is the bench's own, ``store.spend()`` over it is exactly the bench's spend.
Dataset construction and the sweep are budgeted separately, so the guards split it: spend on
replay-only (manifestation) edits, goldens, bless and injection is *construction*; spend on
every other edit is *sweep*. Each phase is held to ``ratchet_bench_usd_cap`` on its own.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from ratchet.bench.dataset import BenchPaths
from ratchet.config.settings import Settings
from ratchet.judge.tavily import FixtureTavily, LiveTavily, TavilySource
from ratchet.orchestrator.context import EditOptions, TavilyFactory
from ratchet.store import Store
from ratchet.store.base import write_json_atomic
from ratchet.util import new_id
from ratchet.watcher.gitops import SkillsRepo

__all__ = ["BenchEnv", "bench_settings", "DEFAULT_WORK", "tavily_factory"]

DEFAULT_WORK = Path("var/bench")
_MANIFEST = "bench/manifest.json"
_CONSTRUCTION = "bench/construction_edits.json"


def bench_settings(base: Settings, paths: BenchPaths, work: Path) -> Settings:
    """``base`` pointed at the bench work area; private mode, no webhooks, fixture Tavily."""
    return base.model_copy(
        update={
            "ratchet_data_dir": work / "data",
            "ratchet_skills_repo": work / "skills",
            "ratchet_scenarios_dir": paths.root,
            "ratchet_public_mode": False,
            "ratchet_simulate_enabled": False,
            "ratchet_notify_webhook": None,
            "ratchet_tavily_mode": "fixtures",
            "ratchet_tavily_fixtures_dir": paths.tavily,
            "ratchet_pii_key_file": base.ratchet_pii_key_file or work / "pii.key",
            # The bench cap is enforced by the runner over the whole bench store; the daily
            # cap must not stop a sweep that the bench cap still allows.
            "ratchet_daily_usd_cap": max(base.ratchet_daily_usd_cap, base.ratchet_bench_usd_cap),
        }
    )


def tavily_factory(live: bool) -> TavilyFactory:
    """``Services.tavily_factory``: fixtures by default; ``--live-tavily`` re-queries."""

    def make(settings: Settings, store: Store, options: EditOptions) -> TavilySource:
        key = settings.tavily_api_key
        if live and key is not None:
            return LiveTavily(key.get_secret_value(), store)
        root = (
            Path(options.tavily_dir) if options.tavily_dir else settings.ratchet_tavily_fixtures_dir
        )
        return FixtureTavily(root)

    return make


@dataclass
class BenchEnv:
    paths: BenchPaths
    work: Path
    settings: Settings
    store: Store
    edits_root: Path
    labels_path: Path
    tavily_dir: Path

    @classmethod
    def open(
        cls,
        paths: BenchPaths,
        work: Path,
        base: Settings,
        *,
        edits_root: Path | None = None,
        labels_path: Path | None = None,
        tavily_dir: Path | None = None,
    ) -> BenchEnv:
        work = work.absolute()
        tavily = (tavily_dir or paths.tavily).absolute()
        settings = bench_settings(base, paths, work).model_copy(
            update={"ratchet_tavily_fixtures_dir": tavily}
        )
        store = Store.open(settings.ratchet_data_dir)
        return cls(
            paths=paths,
            work=work,
            settings=settings,
            store=store,
            edits_root=edits_root or paths.edits,
            labels_path=labels_path or paths.labels,
            tavily_dir=tavily,
        )

    # -- the bench skills repo --------------------------------------------------------------
    @property
    def repo(self) -> SkillsRepo:
        return SkillsRepo(self.settings.ratchet_skills_repo)

    def ensure_repo(self) -> SkillsRepo:
        repo = self.repo
        if not (repo.main_dir / ".git").exists():
            repo.init(self.paths.skills)
        return repo

    def scratch_repo(self) -> SkillsRepo:
        """A fresh independent copy of the bench repo for one edit."""
        dest = self.work / "scratch" / new_id("s")
        return self.ensure_repo().clone_to(dest / "skills")

    @staticmethod
    def drop_scratch(repo: SkillsRepo) -> None:
        shutil.rmtree(repo.main_dir.parent, ignore_errors=True)

    # -- the golden manifest: task_id -> golden run_id ----------------------------------------
    @property
    def manifest_path(self) -> Path:
        return self.store.data_dir / _MANIFEST

    def manifest(self) -> dict[str, str]:
        if not self.manifest_path.is_file():
            return {}
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def save_manifest(self, manifest: dict[str, str]) -> None:
        write_json_atomic(self.manifest_path, dict(sorted(manifest.items())))

    def task_for_run(self) -> dict[str, str]:
        return {run_id: task_id for task_id, run_id in self.manifest().items()}

    def spent_usd(self) -> float:
        return self.store.spend().total_usd

    # -- budget phases: construction vs sweep (budgeted separately) --------------------------
    @property
    def _construction_path(self) -> Path:
        return self.store.data_dir / _CONSTRUCTION

    def construction_edits(self) -> set[str]:
        if not self._construction_path.is_file():
            return set()
        data = json.loads(self._construction_path.read_text(encoding="utf-8"))
        return {str(x) for x in data} if isinstance(data, list) else set()

    def mark_construction(self, edit_id: str) -> None:
        """A replay-only (manifestation) edit: its spend counts as construction, not sweep."""
        write_json_atomic(self._construction_path, sorted(self.construction_edits() | {edit_id}))

    def sweep_spent_usd(self) -> float:
        """Spend on sweep edits: every costed edit that is not a construction edit."""
        rows = self.store.query(
            "SELECT edit_id, SUM(usd) AS usd FROM costs WHERE edit_id IS NOT NULL GROUP BY edit_id"
        )
        skip = self.construction_edits()
        return sum(float(r["usd"] or 0.0) for r in rows if str(r["edit_id"]) not in skip)

    def construction_spent_usd(self) -> float:
        return self.spent_usd() - self.sweep_spent_usd()
