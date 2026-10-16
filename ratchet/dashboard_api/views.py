"""View models for the dashboard API (CONTRACTS §7).

Domain objects (``Verdict``, ``Trace``, ``SkillEdit``, ``PairVerdict``, ``Event``) come from
``ratchet.models`` unchanged. The orchestrator's ``Progress``, ``ScenarioInfo``,
``SimulateStart`` and ``Digest`` (orchestrator-api-cli.md §11) are mirrored here field for
field so the API schema does not depend on the orchestrator being importable; the routes
re-validate the orchestrator objects into these models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictInt, StrictStr

from ratchet.models import (
    Action,
    Decision,
    PairOutcome,
    PairVerdict,
    SkillEdit,
    Tier,
    Trace,
    Verdict,
)

__all__ = [
    "Meta",
    "ScenarioView",
    "SimulateView",
    "VerdictSummary",
    "StageView",
    "Progress",
    "VerdictView",
    "PairView",
    "EditView",
    "TraceSummary",
    "CostTile",
    "LastEditCost",
    "PurposeCost",
    "DayCost",
    "Counterfactual",
    "CostsView",
    "CostWindow",
    "TickerView",
    "DigestView",
    "MetricRow",
    "MetricsView",
    "LogsView",
    "ReblessView",
    "SettingsView",
    "CapView",
    "HealthView",
    "CriteriaIn",
    "DemoEventIn",
    "DemoKind",
    "DEMO_KINDS",
    "ErrorBody",
    "ErrorEnvelope",
    "Page",
]

T = TypeVar("T")


class _View(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Page(BaseModel, Generic[T]):
    """Pagination: ``{"items": [...], "next_cursor": "..." | null}``, newest first."""

    items: list[T]
    next_cursor: str | None = None


class Meta(_View):
    public_mode: bool
    simulate_enabled: bool
    simulate_mode: Literal["live", "replayed"]
    simulated_models: bool
    action_token: str | None
    repo_url: str | None
    video_url: str | None
    bench_report_url: str | None
    version: str
    harness: str
    runner: str
    sandbox: str


class ScenarioView(_View):
    scenario_id: str
    skill: str
    title: str
    learned: str
    catches: str
    expected_decision: Decision
    recorded_verdict_id: str | None = None  # + a global recorded run to open before Simulate


class SimulateView(_View):
    session_hash: str
    scenario_id: str
    edit_id: str | None
    verdict_id: str | None
    status: Literal["running", "queued", "done"]
    queue_position: int | None = None
    eta_s: int | None = None


class VerdictSummary(_View):
    verdict_id: str
    edit_id: str
    decision: Decision
    action: Action
    headline: str
    affected_skills: list[str]
    usd_total: float
    created_at: datetime
    n_pairs: int
    outcome_counts: dict[PairOutcome, int]
    recorded: bool
    simulated_models: bool


class StageView(_View):
    key: str
    label: str
    status: Literal["pending", "active", "done", "skipped"]
    detail: str = ""


class Progress(_View):
    stage: str
    stage_label: str
    k: int
    n: int
    elapsed_s: float
    usd_by_tier: dict[Tier, float]
    stalled: bool
    stages: list[StageView]


class VerdictView(_View):
    verdict_id: str
    state: str
    verdict: Verdict | None
    edit: SkillEdit
    diff: str
    progress: Progress | None
    toast: str | None = None  # + set only on Rollback / Approve responses


class PairView(_View):
    verdict_id: str
    decision: Decision | None  # null while the edit is still being judged
    pair: PairVerdict
    golden: Trace
    replay: Trace
    recorded: bool = False  # + the verdict is a recorded run ("Recorded run" badge)


class EditView(_View):
    edit: SkillEdit
    diff: str
    verdict_id: str | None
    state: str


class TraceSummary(_View):
    run_id: str
    kind: Literal["live", "golden", "replay"]
    task_input: str  # first 400 characters
    task_label: str
    skills_invoked: list[str]
    world_dependent: bool
    n_facts: int
    last_replayed_at: datetime | None
    blessed_at: datetime | None
    created_at: datetime


class CostTile(_View):
    usd_by_tier: dict[Tier, float]
    tokens_by_tier: dict[Tier, int]
    # + prompt-cache share: prompt tokens the upstream served from its prefix cache
    prompt_tokens_by_tier: dict[Tier, int] = Field(default_factory=dict)
    cached_prompt_tokens_by_tier: dict[Tier, int] = Field(default_factory=dict)
    cache_share: float | None = None  # cached / prompt over all tiers; None with no prompt


class LastEditCost(CostTile):
    edit_id: str | None
    verdict_id: str | None
    counterfactual_usd: float


class PurposeCost(_View):
    purpose: str
    tier: Tier
    calls: int
    usd: float
    tokens: int


class DayCost(_View):
    day: str
    usd_by_tier: dict[Tier, float]


class Counterfactual(_View):
    """The savings line: judge spend over every verdict in the window (the last 7 days, or
    ``from``/``to``) — the tiles' population — vs the same judge tokens priced on Ultra."""

    actual_usd: float  # judge spend of the window's verdicts
    all_ultra_usd: float  # the same judge tokens priced on Ultra
    saved_ratio: float
    actual_usd_by_tier: dict[Tier, float] = Field(default_factory=dict)
    n_verdicts: int = 0


class CostWindow(CostTile):
    """``GET /costs?from=&to=``: totals for ``[from, to)``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime


class CostsView(_View):
    today: CostTile
    week: CostTile
    by_purpose: list[PurposeCost]  # last 7 days, or the requested window
    by_day: list[DayCost]  # last 14 days, or the requested window
    last_edit: LastEditCost | None
    counterfactual: Counterfactual
    bench_report_url: str | None
    window: CostWindow | None = None  # + set when from/to are given


class TickerView(_View):
    usd_by_tier: dict[Tier, float]
    total_usd: float
    live: bool  # an edit visible to this session is in flight


class MetricRow(_View):
    """One product-metrics row, as ``ratchet metrics`` prints it."""

    metric: str
    value: str


class MetricsView(_View):
    """``GET /metrics/product`` and ``GET /metrics/demo``."""

    since: datetime
    rows: list[MetricRow]


class LogsView(_View):
    """``GET /edits/{id}/logs``: the edit's batch ``worker.log`` tails plus crash info."""

    lines: list[str]
    truncated: bool = False


class DigestView(_View):
    digest_id: str
    period_start: datetime
    period_end: datetime
    narrative: str
    verdict_ids: list[str]
    merged: list[str]
    blocked: list[str]
    usd: float = 0.0
    simulated_models: bool = False
    metrics: list[MetricRow] = Field(default_factory=list)  # + product metrics table


class ReblessView(_View):
    new_golden_run_id: str
    archived_run_id: str
    toast: str | None = None  # + REBLESS_TOAST


class CapView(_View):
    purpose: str
    usd_per_day: float


class SettingsView(_View):
    caps: list[CapView]
    runner: str
    harness: str
    sandbox: str
    webhook_configured: bool
    models: dict[Tier, str]


class HealthView(_View):
    ok: bool
    components: dict[str, str]


class CriteriaIn(_View):
    criteria: list[Annotated[str, Field(max_length=300)]] = Field(max_length=20)


DemoKind = Literal[
    "demo.landing_view",
    "demo.simulate_click",
    "demo.verdict_view",
    "demo.evidence_open",
    "demo.action_click",
    "demo.repo_click",
    "demo.video_click",
]
DEMO_KINDS: tuple[str, ...] = (
    "demo.landing_view",
    "demo.simulate_click",
    "demo.verdict_view",
    "demo.evidence_open",
    "demo.action_click",
    "demo.repo_click",
    "demo.video_click",
)


class DemoEventIn(_View):
    """Demo funnel event. ``kind`` is also accepted as ``name`` (the dashboard's field).

    Anything not listed here is rejected, so an IP, user agent or free text cannot be smuggled
    in. ``ts`` is accepted and ignored: rows carry the server's time.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kind: DemoKind = Field(validation_alias=AliasChoices("kind", "name"))
    session_hash: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    ts: datetime | None = None
    device: Literal["mobile", "tablet", "desktop"] | None = None
    referrer: Literal["devpost", "youtube", "github", "other", "none"] | None = None
    props: dict[str, StrictStr | StrictInt] = Field(default_factory=dict, max_length=8)


class ErrorBody(_View):
    code: str
    message: str
    request_id: str | None
    details: object | None = None


class ErrorEnvelope(_View):
    error: ErrorBody
