"""Seed the demo agent: skills repo + 25 recorded and blessed goldens (design §6).

1. The operator skills repo is initialized from ``scenarios/skills/``.
2. ``scenarios/golden/tasks.yaml`` is loaded (a list, or ``{tasks: [...]}``; each task has
   ``id``/``task_id``, ``task``/``input``, an optional ``label`` and ``canary: true``).
3. Each task runs live through the in-process proxy (live or simulator, per settings) and
   the mock tools in record mode against ``scenarios/world``: MinimalRunner, the world's
   frozen time, seed = a stable hash of the task id, a fresh empty workspace.
4. Each run is blessed (``orchestrator.bless.bless``).

Idempotent: ``data/demo/manifest.json`` maps task ids to golden run ids and a task whose
golden exists is skipped. ``force=True`` archives the previous demo goldens
(``archived_reason="reseeded"``), re-creates the repo and records everything again. The
three ``canary`` goldens are written to ``data/canaries.json`` for ``select_traces``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

from ratchet.config.settings import Settings
from ratchet.errors import ConfigError
from ratchet.llm.client import ModelClient
from ratchet.log import get_logger
from ratchet.mocktools.world import World
from ratchet.models import Tier
from ratchet.orchestrator.agent import ControlPlane, control_plane, run_task
from ratchet.orchestrator.bless import bless
from ratchet.orchestrator.context import CANARIES_FILE, InProcessProxy
from ratchet.store import Store
from ratchet.store.base import write_json_atomic
from ratchet.util import sha256_hex
from ratchet.watcher.gitops import SkillsRepo

__all__ = [
    "GoldenTask",
    "SeedResult",
    "DEFAULT_FROZEN",
    "load_tasks",
    "task_seed",
    "seed_demo",
    "load_manifest",
    "next_demo_task",
    "estimate_usd",
]

DEFAULT_FROZEN = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)
_TOKENS_PER_TASK = 10_000
_log = get_logger("demo")


@dataclass(frozen=True)
class GoldenTask:
    task_id: str
    task_input: str
    label: str
    canary: bool
    workspace: str | None = None  # a directory under scenarios/workspaces/


class SeedResult(BaseModel):
    repo: str
    recorded: int
    skipped: int
    golden_run_ids: list[str]
    canaries: list[str]
    usd: float


def _field(item: dict[str, object], *keys: str) -> str:
    for key in keys:
        if item.get(key) is not None:
            return str(item[key]).strip()
    return ""


def load_tasks(path: Path) -> list[GoldenTask]:
    if not path.is_file():
        raise ConfigError(f"golden task list {path} not found", code="config_error")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    items = raw.get("tasks", []) if isinstance(raw, dict) else raw
    tasks: list[GoldenTask] = []
    for i, item in enumerate(items if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        task_id = _field(item, "task_id", "id") or f"task-{i + 1:02d}"
        text = _field(item, "task", "input", "task_input", "prompt")
        if not text:
            raise ConfigError(f"golden task {task_id} has no task text", code="config_error")
        label = _field(item, "label", "task_label", "title") or text[:60]
        canary = bool(item.get("canary", False))
        tasks.append(GoldenTask(task_id, text, label, canary, _field(item, "workspace") or None))
    return tasks


def task_seed(task_id: str) -> int:
    """Stable per task, so a re-recording asks the model the same way."""
    return int(sha256_hex(task_id)[:8], 16) % (2**31 - 1)


def estimate_usd(n_tasks: int, tier: Tier = "super") -> float:
    """Budget: about 10k tokens per recorded task on the agent tier."""
    from ratchet.config.pricing import cost_usd

    return cost_usd(tier, n_tasks * _TOKENS_PER_TASK * 9 // 10, n_tasks * _TOKENS_PER_TASK // 10)


def _manifest_path(store: Store) -> Path:
    return store.data_dir / "demo" / "manifest.json"


def load_manifest(store: Store) -> dict[str, str]:
    path = _manifest_path(store)
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _reset(store: Store, repo: SkillsRepo, manifest: dict[str, str]) -> None:
    for run_id in manifest.values():
        if store.has_trace(run_id):
            trace = store.load_trace(run_id)
            store.save_trace(trace.model_copy(update={"archived_reason": "reseeded"}))
    for path in (repo.main_dir, repo.candidate_dir):
        shutil.rmtree(path, ignore_errors=True)


def _is_golden(store: Store, run_id: str) -> bool:
    if not store.has_trace(run_id):
        return False
    trace = store.load_trace(run_id)
    return trace.kind == "golden" and trace.archived_reason is None


async def _record_one(
    settings: Settings,
    store: Store,
    task: GoldenTask,
    *,
    repo: SkillsRepo,
    world: World,
    plane: ControlPlane,
    client: ModelClient,
) -> str:
    with tempfile.TemporaryDirectory(prefix="ratchet-ws-") as tmp:
        workspace = Path(tmp) / "workspace"
        if task.workspace:
            source = settings.ratchet_scenarios_dir / "workspaces" / task.workspace
            await asyncio.to_thread(shutil.copytree, source, workspace)
        trace = await run_task(
            task.task_input,
            settings=settings,
            store=store,
            repo_dir=repo.main_dir,
            world=world,
            frozen_time=world.frozen_time or DEFAULT_FROZEN,
            seed=task_seed(task.task_id),
            task_label=task.label,
            workspace=workspace,
            plane=plane,
        )
    golden = await bless(store, settings, trace.run_id, client=client)
    return golden.run_id


async def seed_demo(
    settings: Settings,
    store: Store,
    *,
    force: bool = False,
    task_ids: Sequence[str] | None = None,
    on_task: Callable[[GoldenTask, str], None] | None = None,
) -> SeedResult:
    """Seed (or top up) the demo; see the module docstring."""
    # The operator seeds the host before it goes public; record mode needs private mode.
    settings = settings.model_copy(update={"ratchet_public_mode": False})
    root = settings.ratchet_scenarios_dir
    repo = SkillsRepo(settings.ratchet_skills_repo)
    manifest = await asyncio.to_thread(load_manifest, store)
    if force:
        await asyncio.to_thread(_reset, store, repo, manifest)
        manifest = {}
    if not (repo.main_dir / ".git").exists():
        await asyncio.to_thread(repo.init, root / "skills")
    world = await asyncio.to_thread(World.load, root / "world")
    tasks = load_tasks(root / "golden" / "tasks.yaml")
    if task_ids is not None:
        tasks = [t for t in tasks if t.task_id in set(task_ids)]
    spent_before = (await asyncio.to_thread(store.spend)).total_usd
    proxy = InProcessProxy(settings, store)
    client = ModelClient(run_id="bless_demo", settings=settings, http=proxy.client)
    recorded = skipped = 0
    try:
        with control_plane(settings, store, world) as plane:
            for task in tasks:
                existing = manifest.get(task.task_id)
                done = False
                if existing is not None:
                    done = await asyncio.to_thread(_is_golden, store, existing)
                if done:
                    skipped += 1
                    continue
                run_id = await _record_one(
                    settings, store, task, repo=repo, world=world, plane=plane, client=client
                )
                manifest[task.task_id] = run_id
                await asyncio.to_thread(write_json_atomic, _manifest_path(store), manifest)
                recorded += 1
                if on_task is not None:
                    on_task(task, run_id)
    finally:
        await proxy.aclose()
    canaries = [manifest[t.task_id] for t in tasks if t.canary and t.task_id in manifest]
    if canaries or task_ids is None:
        await asyncio.to_thread(write_json_atomic, store.data_dir / CANARIES_FILE, canaries)
    spent = (await asyncio.to_thread(store.spend)).total_usd - spent_before
    _log.info("demo.seeded", recorded=recorded, skipped=skipped, n_canaries=len(canaries))
    return SeedResult(
        repo=str(repo.main_dir),
        recorded=recorded,
        skipped=skipped,
        golden_run_ids=[manifest[t.task_id] for t in tasks if t.task_id in manifest],
        canaries=canaries,
        usd=round(spent, 6),
    )


def next_demo_task(settings: Settings, store: Store) -> GoldenTask:
    """``ratchet agent start --demo``: the next demo task, round-robin."""
    tasks = load_tasks(settings.ratchet_scenarios_dir / "golden" / "tasks.yaml")
    if not tasks:
        raise ConfigError("the demo has no tasks", code="config_error")
    cursor_path = store.data_dir / "demo" / "agent_cursor.json"
    cursor = 0
    if cursor_path.is_file():
        cursor = int(json.loads(cursor_path.read_text(encoding="utf-8")).get("next", 0))
    write_json_atomic(cursor_path, {"next": (cursor + 1) % len(tasks)})
    return tasks[cursor % len(tasks)]
