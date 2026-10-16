"""Ratchet's data contract.

Every object that crosses a process or file boundary is defined here. Field names of the
core contract are final; fields added beyond it are marked ``# +`` with the reason they
exist. Renaming a field needs an ADR (docs/adr).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "Tier",
    "TIERS",
    "SideEffectClass",
    "HARD_SIDE_EFFECTS",
    "Purpose",
    "PairOutcome",
    "Decision",
    "Action",
    "OpinionVerdict",
    "LLMCall",
    "ToolCall",
    "Step",
    "WorldFact",
    "EnvSnapshot",
    "Trace",
    "SkillEdit",
    "ReplayBatch",
    "ReplayResult",
    "Level0Finding",
    "CriterionCheck",
    "JudgeOpinion",
    "DriftCheck",
    "PairVerdict",
    "Verdict",
    "StageTiming",
    "HarnessResult",
    "Event",
    "Cassette",
]

Tier = Literal["nano", "super", "ultra"]
TIERS: tuple[Tier, ...] = ("nano", "super", "ultra")

SideEffectClass = Literal[
    "none",
    "read",
    "write_reversible",
    "write_irreversible",
    "destructive",
    "external_send",
    "spend",
]
# A *new* call of one of these classes in a replay is a Level 0 hard fail.
HARD_SIDE_EFFECTS: frozenset[str] = frozenset(
    {"destructive", "write_irreversible", "external_send", "spend"}
)

Purpose = Literal[
    "agent", "screen", "check", "adjudicate", "extract", "narrative", "bench", "embed"
]
OpinionVerdict = Literal["equivalent", "regressed", "improved", "uncertain"]
PairOutcome = Literal["pass", "regression", "drift", "improved", "uncertain", "failed"]
Decision = Literal["PASS", "WARN", "BLOCK"]
Action = Literal["merged", "held", "reverted"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LLMCall(_Model):
    kind: Literal["llm"] = "llm"  # + discriminator for Step
    step_id: str
    tier: Tier
    model: str
    request_hash: str  # sha256 of normalized request (messages, tools, params)
    messages_ref: str  # path to full request JSON
    response_ref: str  # path to full response JSON
    prompt_tokens: int
    completion_tokens: int
    usd: float
    latency_ms: int
    started_at: datetime
    purpose: Purpose = "agent"  # + proxy interface tag
    api: Literal["chat", "responses"] = "chat"  # + /v1/responses is recorded with api
    usage_estimated: bool = False  # + stream usage missing, counted approximately
    tool_calls_requested: list[str] = Field(
        default_factory=list
    )  # + tool names the model asked for
    content_preview: str = ""  # + first 400 chars of the assistant text, for judge step summaries
    cached_prompt_tokens: int = 0  # + prompt tokens served from the upstream's prompt cache


class ToolCall(_Model):
    kind: Literal["tool"] = "tool"  # + discriminator for Step
    step_id: str
    tool: str  # e.g. "github.close_issue", "gmail.send", "fs.write", "shell.run"
    args_norm: dict[str, object]  # sorted keys, tokenized PII, volatile fields dropped
    args_hash: str
    result_hash: str | None
    side_effect: SideEffectClass
    source: Literal["cassette", "live", "local"]
    cassette_key: str | None
    result_preview: str = ""  # + first 400 chars of the result (a Level 2 judge input)
    blocked: bool = False  # + answered 409 ratchet_blocked in replay
    started_at: datetime | None = None  # + ordering when merging proxy and mock-server steps


Step = Annotated[LLMCall | ToolCall, Field(discriminator="kind")]


class WorldFact(_Model):
    fact_id: str
    statement: str  # "EUR/USD rate on 2026-10-14 was 1.08"
    source_step: str  # step_id where it entered the trace
    volatile: bool  # true if expected to change over time
    query_hint: str  # Tavily query to re-ground this fact
    value: str = ""  # + the bare observed value ("1.08"), compared against fresh evidence


class EnvSnapshot(_Model):
    workspace_ref: str  # tarball path of the agent workspace at task start
    frozen_time: datetime  # time injected into the sandbox during replay
    harness_version: str  # "<adapter>/<version>", e.g. "minimal/0.1.0"
    skills_commit: str  # commit on main the golden ran against
    world_ref: str | None = None  # + world-state fixture the tool adapters served (demo/bench)


class Trace(_Model):
    run_id: str
    kind: Literal["live", "golden", "replay"]
    task_input: str  # the user message that started the task
    steps: list[Step]
    final_output: str
    artifacts_ref: str | None  # tarball of files created/changed
    skills_invoked: list[str]
    world_dependent: bool
    world_facts: list[WorldFact]
    env: EnvSnapshot
    agent_tier: Tier
    seed: int
    created_at: datetime
    golden_of: str | None  # for replays: the golden run_id
    candidate_commit: str | None
    success_criteria: list[str] = Field(default_factory=list)  # + inferred at bless
    archived_reason: str | None = None  # + rebless archives the old golden
    blessed_at: datetime | None = None  # + for metrics
    harness_ok: bool = True  # + harness crash in replay
    error_code: str | None = None  # + sandbox_failed | harness_failed
    task_label: str = ""  # + short human title for the UI ("Triage acme/widgets backlog")


class SkillEdit(_Model):
    edit_id: str
    commit: str  # on candidate
    parent: str  # commit on main
    files: list[str]
    diff_ref: str
    affected_skills: list[str]
    author: Literal["agent", "user", "seeded"]
    detected_at: datetime
    summary: str = ""  # + "what changed" one-liner for the dashboard
    session_hash: str | None = None  # + Simulate sessions are isolated


class ReplayBatch(_Model):
    batch_id: str
    edit_id: str
    golden_run_ids: list[str]
    skills_tar_ref: str  # candidate checkout
    cassettes_ref: str
    worker_image: str
    agent_tier: Tier
    sandbox: Literal["openshell", "docker", "process"]  # + process: dev-only, ADR 0017
    timeout_s: int = 600
    candidate_commit: str = ""  # + stamped on every replay trace
    harness: Literal["hermes", "minimal"] = "minimal"  # + harness adapter selection
    world_ref: str | None = None  # + world overlay for scenario 3 / bench D1
    # + spend caps: USD left per cap at dispatch ("daily", purpose names, "bench"),
    # computed from the control-plane ledger; the sidecar proxy enforces it (None = no cap).
    budget_allowance_usd: dict[str, float] | None = None


class ReplayResult(_Model):
    batch_id: str
    replays: list[Trace]
    unexpected_side_effects: list[ToolCall]  # cassette misses with side_effect != read
    worker_log_ref: str
    status: Literal["ok", "partial", "failed"]
    unexpected_by_run: dict[str, list[ToolCall]] = Field(default_factory=dict)  # + per replay
    failed_goldens: dict[str, str] = Field(default_factory=dict)  # + golden run_id -> error code


class Level0Finding(_Model):
    golden_run_id: str
    replay_run_id: str
    new_calls: list[ToolCall]
    missing_calls: list[ToolCall]
    changed_args: list[tuple[ToolCall, ToolCall]]
    hard_fail: bool  # any new call with side_effect in HARD_SIDE_EFFECTS
    missing_hard: bool = False  # + a missing write_irreversible/external_send call
    output_similarity: float = 1.0  # golden vs replay final output, 0..1 (ADR 0024)


class CriterionCheck(_Model):
    criterion: str
    met: bool
    evidence_steps: list[str] = Field(default_factory=list)


class JudgeOpinion(_Model):
    tier: Tier
    verdict: OpinionVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str]
    evidence_steps: list[str]  # step_ids cited
    usd: float
    prompt_hash: str = ""  # + prompts versioned by file hash
    truncated: bool = False  # + evidence shortened to fit the context budget
    criteria_met: list[CriterionCheck] = Field(default_factory=list)  # + check findings
    causal_explanation: str | None = None  # + Ultra adjudication
    possible_drift_fact: str | None = None  # + Ultra adjudication
    failure_summary: str | None = None  # + what got worse, a short verb phrase (headline)
    caused_by_diff: bool | None = None  # + causality: Ultra attributes it to the edit
    prompt_tokens: int = 0  # + cost panel token counts
    completion_tokens: int = 0  # +


class DriftCheck(_Model):
    fact_id: str
    query: str
    tavily_results_ref: str
    changed: bool
    old_value: str
    new_value: str | None
    confidence: float
    statement: str = ""  # + drift card: fact statement
    source_url: str | None = None  # + drift card: Tavily source link
    source_title: str | None = None  # +
    status: Literal["checked", "unavailable"] = "checked"  # + drift_unavailable
    attributable: bool | None = None  # + attribute_drift ruling
    ruling: str = ""  # + one-line ruling shown on the card
    replay_value: str | None = None  # + the value the replay's own world tool reported


class PairVerdict(_Model):
    golden_run_id: str
    replay_run_id: str
    level0: Level0Finding
    opinions: list[JudgeOpinion]
    drift: list[DriftCheck]
    outcome: PairOutcome  # + "failed" ("Could not replay")
    usd: float
    error_code: str | None = None  # + why the pair failed or is uncertain
    task_input: str = ""  # + denormalized for the pair grid
    task_label: str = ""  # +
    decided_by: Literal["level0", "nano", "super", "ultra", "drift", "infra", "control"] = (
        "nano"  # + "control": ADR 0023 control replay showed nondeterminism
    )
    reason: str = ""  # + one-sentence explanation line per pair outcome
    control_run_id: str | None = None  # + ADR 0023: the control replay (unchanged skill)
    # + ADR 0023: did the control match the golden ("agrees") or also differ ("diverges")
    control_outcome: Literal["agrees", "diverges"] | None = None


class StageTiming(_Model):
    stage: str
    started_at: datetime
    elapsed_ms: int


class Verdict(_Model):
    verdict_id: str
    edit_id: str
    pairs: list[PairVerdict]
    decision: Decision
    action: Action
    narrative: str  # Ultra, <= 180 words
    cost: dict[Tier, float]
    duration_s: int
    created_at: datetime
    reasons: list[str] = Field(default_factory=list)  # + verdict reason codes
    headline: str = ""  # + first sentence, validated against the headline templates
    tokens: dict[Tier, int] = Field(default_factory=dict)  # + cost panel
    counterfactual_usd: float = 0.0  # + all-Ultra counterfactual
    replay_cost: dict[Tier, float] = Field(default_factory=dict)  # + agent spend on replays
    action_commit: str | None = None  # + merge or revert commit
    stages: list[StageTiming] = Field(default_factory=list)  # + stage timeline
    recorded: bool = False  # + served in RATCHET_SIMULATE_MODE=replayed
    simulated_models: bool = False  # + produced by the offline simulator, not Nemotron
    rolled_back_by: str | None = None  # + manual rollback actor
    rolled_back_at: datetime | None = None  # +
    approved_at: datetime | None = None  # +
    session_hash: str | None = None  # +
    affected_skills: list[str] = Field(default_factory=list)  # + denormalized for headers
    author: str = ""  # +
    runner: str = ""  # + debug drawer
    batch_ids: list[str] = Field(default_factory=list)  # +


class HarnessResult(_Model):
    """What a harness adapter returns for one task."""

    final_output: str
    exit_ok: bool
    stdout_ref: str
    stderr_ref: str
    skills_invoked: list[str]
    harness_version: str


class Event(_Model):
    """One row of the events table, the product's spine."""

    id: int
    ts: datetime
    kind: str
    ref: str
    payload: dict[str, object]
    session_hash: str | None = None


class Cassette(_Model):
    """Storage layout: data/cassettes/<tool>/<args_hash>.json."""

    tool: str
    args_norm: dict[str, object]
    response: dict[str, object]
    recorded_at: datetime
    side_effect: SideEffectClass
    args_raw_hash: str = ""  # +
    later_responses: list[dict[str, object]] = Field(default_factory=list)  # + repeat-call queue
