"""E2E fixtures and steps (imported by tests/e2e/conftest.py and the orchestrator chaos
tests): the demo is seeded once per session (simulator upstream, process runner,
process sandbox) into a template, then copied for each test so tests never share state.

``demo`` gives a fresh data dir + PII key + an independent clone of the skills repo (same
commit ids, so the goldens' ``skills_commit`` still resolves).
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from ratchet.config.settings import Settings, configure, reset_settings
from ratchet.models import SkillEdit, Verdict
from ratchet.orchestrator.demo import seed_demo
from ratchet.orchestrator.ids import verdict_id_for
from ratchet.orchestrator.scenarios import get_scenario
from ratchet.orchestrator.service import Orchestrator
from ratchet.store import Store
from ratchet.watcher.edits import build_edit
from ratchet.watcher.gitops import SkillsRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = REPO_ROOT / "scenarios"


def make_settings(root: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "ratchet_data_dir": root / "data",
        "ratchet_skills_repo": root / "skills",
        "ratchet_upstream": "sim",
        "ratchet_job_runner": "local",
        "ratchet_local_runner_mode": "process",
        "ratchet_sandbox": "process",
        "ratchet_harness": "minimal",
        "ratchet_scenarios_dir": SCENARIOS,
        "ratchet_tavily_fixtures_dir": REPO_ROOT / "tests" / "fixtures" / "tavily",
        "ratchet_tavily_mode": "fixtures",
        "ratchet_log_level": "ERROR",
        "ratchet_batch_size": 3,
        "ratchet_simulate_enabled": True,
        "ratchet_public_mode": False,
        "ratchet_notify_webhook": None,
        "nebius_token_factory_key": None,
        "tavily_api_key": None,
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def demo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("demo-template")
    settings = make_settings(root)
    configure(settings)
    try:
        store = Store.open(settings.ratchet_data_dir)
        asyncio.run(seed_demo(settings, store))
    finally:
        reset_settings()
    return root


@dataclass
class Demo:
    root: Path
    settings: Settings
    store: Store

    @property
    def repo(self) -> SkillsRepo:
        return SkillsRepo(self.settings.ratchet_skills_repo)

    def with_settings(self, **overrides: object) -> Demo:
        settings = make_settings(self.root, **overrides)
        configure(settings)
        return Demo(self.root, settings, self.store)


@pytest.fixture
def demo(tmp_path: Path, demo_template: Path) -> Iterator[Demo]:
    shutil.copytree(demo_template / "data", tmp_path / "data")
    if (demo_template / "pii.key").is_file():
        shutil.copyfile(demo_template / "pii.key", tmp_path / "pii.key")
    SkillsRepo(demo_template / "skills").clone_to(tmp_path / "skills")
    settings = make_settings(tmp_path)
    configure(settings)
    try:
        yield Demo(tmp_path, settings, Store.open(settings.ratchet_data_dir))
    finally:
        reset_settings()


class Hook:
    """A webhook receiver (httpx.MockTransport) for notification assertions."""

    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json

        self.payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def orchestrator_for(demo: Demo, **kwargs: object) -> Orchestrator:
    defaults: dict[str, object] = {
        "poll_s": 0.2,
        "submit_backoff_s": (0.05,),
        "digest_enabled": False,
        "reset_interval_s": 3600.0,
    }
    defaults.update(kwargs)
    return Orchestrator(demo.settings, demo.store, **defaults)  # type: ignore[arg-type]


@pytest.fixture
async def running(demo: Demo) -> AsyncIterator[Orchestrator]:
    orchestrator = orchestrator_for(demo)
    await orchestrator.start(watch=False)
    try:
        yield orchestrator
    finally:
        await orchestrator.stop()


def scenario_files(scenario_id: str) -> dict[str, str | None]:
    return get_scenario(SCENARIOS, scenario_id).edit_files()


def submit_operator_edit(
    demo: Demo,
    orchestrator: Orchestrator,
    files: dict[str, str | None],
    *,
    parent: str | None = None,
) -> SkillEdit:
    """Commit ``files`` on the operator repo's candidate and hand the edit over, the way
    the watcher does."""
    repo = demo.repo
    head = repo.apply_files(files, message="edit under test", author="user")
    edit, diff = build_edit(repo, commit=head, parent=parent, author="user")
    orchestrator.submit_edit(edit, diff)
    return edit


async def verdict_after(demo: Demo, orchestrator: Orchestrator, edit: SkillEdit) -> Verdict:
    await orchestrator.wait_idle(120)
    return demo.store.load_verdict(verdict_id_for(edit.edit_id))


def kinds(demo: Demo, ref: str) -> list[str]:
    return [e.kind for e in demo.store.events(ref=ref, limit=1000)]
