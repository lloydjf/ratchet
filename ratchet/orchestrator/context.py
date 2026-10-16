"""Everything one edit's pipeline needs, resolved once per run (design §1, §4, §5).

``Services`` holds what the orchestrator shares across edits (store, job runners, the judge
HTTP client over the in-process proxy, the notifier client). ``EditContext`` adds the
per-edit pieces: the skills repo the edit lives in (the operator repo, or a Simulate session
copy), the judge ``ModelClient`` (``judge_<hex>``, costed under the edit), the Tavily source
and the persisted ``EditOptions`` (attempt number, scenario, world overlay).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from ratchet.config.settings import Settings
from ratchet.judge.cascade import CascadeConfig
from ratchet.judge.tavily import FixtureTavily, LiveTavily, TavilySource
from ratchet.llm.client import ModelClient
from ratchet.models import SkillEdit
from ratchet.orchestrator.embed import Embedder, get_embedder
from ratchet.orchestrator.ids import judge_run_id
from ratchet.replay.jobs import JobRunner, LocalJobRunner, get_runner
from ratchet.store import Store
from ratchet.store.base import write_json_atomic
from ratchet.watcher.gitops import SkillsRepo

__all__ = [
    "EditOptions",
    "Services",
    "EditContext",
    "load_options",
    "save_options",
    "attempt_dir",
    "build_context",
    "InProcessProxy",
    "tavily_source",
    "RETRY_RUNNER",
    "make_embedder",
    "default_runner",
    "retry_runner",
    "operator_repo",
    "load_canaries",
    "CANARIES_FILE",
]

CANARIES_FILE = "canaries.json"
_PROXY_BASE = "http://ratchet-proxy.internal"

TavilyFactory = Callable[[Settings, Store, "EditOptions"], TavilySource]


class EditOptions(BaseModel):
    """``edits/<edit_id>/options.json``: per-edit settings that survive a restart."""

    model_config = ConfigDict(extra="ignore")

    attempt: int = 1
    scenario_id: str | None = None
    repo_dir: str | None = None  # a Simulate session copy; default: the operator repo
    world_ref: str | None = None  # cassette overlay served before the main cassettes
    tavily_dir: str | None = None  # scenario-scoped Tavily fixtures
    tavily_fixtures_only: bool = False  # use tavily_dir even when Tavily is live
    runner: str | None = None  # "local" | "nebius" override (ratchet replay --runner)
    # ADR 0023: only use control results already in the attempt (a bench re-judge copies the
    # source run's); never dispatch a new control replay.
    reuse_controls: bool = False


def _options_path(store: Store, edit_id: str) -> Path:
    return store.edit_dir(edit_id) / "options.json"


def load_options(store: Store, edit_id: str) -> EditOptions:
    path = _options_path(store, edit_id)
    if not path.exists():
        return EditOptions()
    return EditOptions.model_validate_json(path.read_text(encoding="utf-8"))


def save_options(store: Store, edit_id: str, options: EditOptions) -> None:
    write_json_atomic(_options_path(store, edit_id), options)


def attempt_dir(store: Store, edit_id: str, attempt: int) -> Path:
    """Per-attempt working files (judged pairs, stage timings); a replay starts a new one."""
    return store.edit_dir(edit_id) / f"attempt_{attempt}"


def load_canaries(store: Store) -> list[str]:
    path = store.data_dir / CANARIES_FILE
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [str(x) for x in data] if isinstance(data, list) else []


class InProcessProxy:
    """A capture proxy app reached through ``httpx.ASGITransport`` (no network port).

    The judge, bless and digest clients use it; ``aclose`` also closes the proxy's upstream
    pool, since an ASGI transport never runs the app's lifespan.
    """

    def __init__(
        self,
        settings: Settings,
        store: Store,
        *,
        upstream_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        from ratchet.proxy.app import create_app

        self.app = create_app(settings=settings, store=store, upstream_transport=upstream_transport)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url=_PROXY_BASE,
            timeout=httpx.Timeout(310.0, connect=10.0),
        )

    async def aclose(self) -> None:
        await self.client.aclose()
        await self.app.state.proxy.upstream.aclose()


def default_runner(settings: Settings, store: Store) -> JobRunner:
    """The configured runner; the local one keeps its work dirs under the data dir so a
    restarted orchestrator can still read a finished batch's output."""
    if settings.ratchet_job_runner == "local":
        return LocalJobRunner(
            settings.ratchet_local_runner_mode, settings=settings, work_root=store.data_dir / "jobs"
        )
    return get_runner(settings)


def retry_runner(settings: Settings, store: Store) -> JobRunner:
    """A stalled or lost batch is retried once on the local runner."""
    return LocalJobRunner(
        settings.ratchet_local_runner_mode,
        settings=settings,
        work_root=store.data_dir / "jobs-retry",
    )


def operator_repo(settings: Settings) -> SkillsRepo:
    return SkillsRepo(settings.ratchet_skills_repo)


@dataclass
class Services:
    settings: Settings
    store: Store
    runner: JobRunner
    retry: JobRunner
    judge_http: httpx.AsyncClient
    embedder: Embedder
    notify_http: httpx.AsyncClient | None = None
    cascade: CascadeConfig = field(default_factory=CascadeConfig)
    tavily_factory: TavilyFactory | None = None
    poll_s: float = 2.0
    stall_s: float = 120.0
    provision_s: float = 900.0  # a job still provisioning (no worker yet) gets this budget
    submit_backoff_s: tuple[float, ...] = (2.0, 4.0, 8.0)
    judge_concurrency: int = 8

    extra_runners: dict[str, JobRunner] = field(default_factory=dict)

    def runner_for(self, key: str) -> JobRunner:
        """The runner a batch row names (``batches.runner``)."""
        if key == RETRY_RUNNER:
            return self.retry
        return self.extra_runners.get(key, self.runner)

    def pick(self, override: str | None) -> tuple[str, JobRunner]:
        """``(key, runner)`` for a new batch; ``override`` is ``ratchet replay --runner``."""
        primary_is_nebius = self.runner.name.startswith("nebius")
        if override == "local" and primary_is_nebius:
            return RETRY_RUNNER, self.retry
        if override == "nebius" and not primary_is_nebius:
            runner = self.extra_runners.get("nebius")
            if runner is None:
                runner = self.extra_runners["nebius"] = get_runner(
                    self.settings.model_copy(update={"ratchet_job_runner": "nebius"})
                )
            return "nebius", runner
        return self.runner.name, self.runner


RETRY_RUNNER = "local-retry"


@dataclass(frozen=True)
class EditContext:
    services: Services
    edit: SkillEdit
    options: EditOptions
    repo: SkillsRepo
    client: ModelClient
    tavily: TavilySource

    @property
    def settings(self) -> Settings:
        return self.services.settings

    @property
    def store(self) -> Store:
        return self.services.store

    @property
    def edit_id(self) -> str:
        return self.edit.edit_id

    @property
    def session_hash(self) -> str | None:
        return self.edit.session_hash

    @property
    def work(self) -> Path:
        return attempt_dir(self.store, self.edit_id, self.options.attempt)


def tavily_source(settings: Settings, store: Store, options: EditOptions) -> TavilySource:
    key = settings.tavily_api_key
    if options.tavily_fixtures_only and options.tavily_dir:
        return FixtureTavily(Path(options.tavily_dir))
    if settings.ratchet_tavily_mode == "live" and key is not None:
        return LiveTavily(key.get_secret_value(), store)
    if options.tavily_dir:
        return FixtureTavily(Path(options.tavily_dir))
    return FixtureTavily(settings.ratchet_tavily_fixtures_dir)


def build_context(services: Services, edit_id: str) -> EditContext:
    store, settings = services.store, services.settings
    edit = store.load_edit(edit_id)
    options = load_options(store, edit_id)
    repo = SkillsRepo(Path(options.repo_dir)) if options.repo_dir else operator_repo(settings)
    client = ModelClient(
        run_id=judge_run_id(edit_id),
        edit_id=edit_id,
        session_hash=edit.session_hash,
        settings=settings,
        http=services.judge_http,
    )
    factory = services.tavily_factory or tavily_source
    return EditContext(services, edit, options, repo, client, factory(settings, store, options))


def make_embedder(settings: Settings, http: httpx.AsyncClient) -> Embedder:
    return get_embedder(settings, http=http)
