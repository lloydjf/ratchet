/**
 * Ratchet API types.
 *
 * Mirrors `ratchet/models.py` (including the additive `# +` fields) and the view models in
 * docs/CONTRACTS.md §7 field for field. JSON uses the Python names; datetimes are ISO
 * 8601 strings with an offset. Hand-written (the generated schema lives in
 * `openapi.gen.ts`) — keep everything in this one file.
 */

// ---------------------------------------------------------------- literals
export type Tier = 'nano' | 'super' | 'ultra'
export const TIERS: readonly Tier[] = ['nano', 'super', 'ultra'] as const

export type SideEffectClass =
  | 'none'
  | 'read'
  | 'write_reversible'
  | 'write_irreversible'
  | 'destructive'
  | 'external_send'
  | 'spend'
export const HARD_SIDE_EFFECTS: ReadonlySet<SideEffectClass> = new Set([
  'destructive',
  'write_irreversible',
  'external_send',
  'spend',
])

export type Purpose = 'agent' | 'screen' | 'check' | 'adjudicate' | 'extract' | 'narrative' | 'bench' | 'embed'
export type OpinionVerdict = 'equivalent' | 'regressed' | 'improved' | 'uncertain'
export type PairOutcome = 'pass' | 'regression' | 'drift' | 'improved' | 'uncertain' | 'failed'
export type Decision = 'PASS' | 'WARN' | 'BLOCK'
export type Action = 'merged' | 'held' | 'reverted'
export type ISODateTime = string

// ---------------------------------------------------------------- steps
export interface LLMCall {
  kind: 'llm'
  step_id: string
  tier: Tier
  model: string
  request_hash: string
  messages_ref: string
  response_ref: string
  prompt_tokens: number
  /** Prompt tokens the provider served from its prompt cache (0 when not reported). */
  cached_prompt_tokens: number
  completion_tokens: number
  usd: number
  latency_ms: number
  started_at: ISODateTime
  purpose: Purpose
  api: 'chat' | 'responses'
  usage_estimated: boolean
  tool_calls_requested: string[]
  content_preview: string
}

export interface ToolCall {
  kind: 'tool'
  step_id: string
  tool: string
  args_norm: Record<string, unknown>
  args_hash: string
  result_hash: string | null
  side_effect: SideEffectClass
  source: 'cassette' | 'live' | 'local'
  cassette_key: string | null
  result_preview: string
  blocked: boolean
  started_at: ISODateTime | null
}

/** Discriminated by `kind`. */
export type Step = LLMCall | ToolCall

// ---------------------------------------------------------------- traces
export interface WorldFact {
  fact_id: string
  statement: string
  source_step: string
  volatile: boolean
  query_hint: string
  value: string
}

export interface EnvSnapshot {
  workspace_ref: string
  frozen_time: ISODateTime
  harness_version: string
  skills_commit: string
  world_ref: string | null
}

export interface Trace {
  run_id: string
  kind: 'live' | 'golden' | 'replay'
  task_input: string
  steps: Step[]
  final_output: string
  artifacts_ref: string | null
  skills_invoked: string[]
  world_dependent: boolean
  world_facts: WorldFact[]
  env: EnvSnapshot
  agent_tier: Tier
  seed: number
  created_at: ISODateTime
  golden_of: string | null
  candidate_commit: string | null
  success_criteria: string[]
  archived_reason: string | null
  blessed_at: ISODateTime | null
  harness_ok: boolean
  error_code: string | null
  task_label: string
}

export interface SkillEdit {
  edit_id: string
  commit: string
  parent: string
  files: string[]
  diff_ref: string
  affected_skills: string[]
  author: 'agent' | 'user' | 'seeded'
  detected_at: ISODateTime
  summary: string
  session_hash: string | null
}

// ---------------------------------------------------------------- judging
export interface Level0Finding {
  golden_run_id: string
  replay_run_id: string
  new_calls: ToolCall[]
  missing_calls: ToolCall[]
  /** Python `list[tuple[ToolCall, ToolCall]]` serializes as two-element arrays. */
  changed_args: [ToolCall, ToolCall][]
  hard_fail: boolean
  missing_hard: boolean
  /** ADR 0024: how alike the replay's final output is to the golden's, 0..1 (1.0 = identical). */
  output_similarity: number
}

export interface CriterionCheck {
  criterion: string
  met: boolean
  evidence_steps: string[]
}

export interface JudgeOpinion {
  tier: Tier
  verdict: OpinionVerdict
  /** 0..1 */
  confidence: number
  reasons: string[]
  evidence_steps: string[]
  usd: number
  prompt_hash: string
  truncated: boolean
  criteria_met: CriterionCheck[]
  causal_explanation: string | null
  possible_drift_fact: string | null
  /** Whether the judge attributed the difference to the skill diff (null when it did not say). */
  caused_by_diff?: boolean | null
  /** Regressed opinions: what got worse as a short verb phrase ("drops the VAT line"). */
  failure_summary?: string | null
  prompt_tokens: number
  completion_tokens: number
}

export interface DriftCheck {
  fact_id: string
  query: string
  tavily_results_ref: string
  changed: boolean
  old_value: string
  new_value: string | null
  confidence: number
  /** The value the replay's own world tool reported (what the fact check compared against). */
  replay_value?: string | null
  statement: string
  source_url: string | null
  source_title: string | null
  status: 'checked' | 'unavailable'
  attributable: boolean | null
  ruling: string
}

export type DecidedBy = 'level0' | 'nano' | 'super' | 'ultra' | 'drift' | 'infra' | 'control'

/** ADR 0023: what the control replay (the unchanged skill) showed on a contested pair. */
export type ControlOutcome = 'agrees' | 'diverges'

export interface PairVerdict {
  golden_run_id: string
  replay_run_id: string
  level0: Level0Finding
  opinions: JudgeOpinion[]
  drift: DriftCheck[]
  outcome: PairOutcome
  usd: number
  error_code: string | null
  task_input: string
  task_label: string
  decided_by: DecidedBy
  reason: string
  /** ADR 0023: the control replay of this golden on the parent tree, when one ran. */
  control_run_id?: string | null
  /** `agrees`: the control matched the golden; `diverges`: it differed too (nondeterminism). */
  control_outcome?: ControlOutcome | null
}

export interface StageTiming {
  stage: string
  started_at: ISODateTime
  elapsed_ms: number
}

export type TierMap<T> = Partial<Record<Tier, T>>

export interface Verdict {
  verdict_id: string
  edit_id: string
  pairs: PairVerdict[]
  decision: Decision
  action: Action
  narrative: string
  cost: TierMap<number>
  duration_s: number
  created_at: ISODateTime
  reasons: string[]
  headline: string
  tokens: TierMap<number>
  counterfactual_usd: number
  /** Agent spend on the replays themselves (not judging). Older verdicts may omit it. */
  replay_cost?: Partial<TierMap<number>>
  action_commit: string | null
  stages: StageTiming[]
  recorded: boolean
  simulated_models: boolean
  rolled_back_by: string | null
  rolled_back_at: ISODateTime | null
  approved_at: ISODateTime | null
  session_hash: string | null
  affected_skills: string[]
  author: string
  runner: string
  batch_ids: string[]
}

export interface Event {
  id: number
  ts: ISODateTime
  kind: EventKind | (string & {})
  ref: string
  payload: Record<string, unknown>
  session_hash: string | null
}

/**
 * Every event kind the backend emits (as found across `ratchet/`). The SSE client registers a
 * listener per kind (EventSource only delivers named events it listens for) and also
 * catches up through `/events?since=` so a kind missing here is never silently dropped.
 */
export const EVENT_KINDS = [
  'run.started',
  'run.finished',
  'trace.blessed',
  'trace.unblessed',
  'trace.criteria_set',
  'edit.detected',
  'edit.selected',
  'batch.submitted',
  'batch.heartbeat',
  'batch.finished',
  'pair.level0',
  'pair.opinion',
  'pair.drift',
  'pair.control',
  'pair.decided',
  'pair.reblessed',
  'verdict.decided',
  'verdict.acted',
  'verdict.notified',
  'verdict.rolled_back',
  'simulate.started',
  'simulate.finished',
  'simulate.recorded',
  'simulate.reset',
  'digest.written',
  'digest.failed',
  'orchestrator.state',
  'orchestrator.batch_stalled',
  'notify.delivered',
  'notify.failed',
  'demo.seeded',
] as const
export type EventKind = (typeof EVENT_KINDS)[number]

/** `verdict.acted` payload (ratchet/orchestrator/actions.py). `toast` is the confirmation line to show. */
export interface VerdictActedPayload {
  verdict_id: string
  edit_id?: string
  action: Action
  commit?: string | null
  actor?: string
  /** True when Approve ran on a reverted verdict (BLOCK or rolled back): the edit was re-applied. */
  override?: boolean
  toast?: string
}

/** `verdict.rolled_back` payload. `toast` says whether main changed (merged edits) or not. */
export interface VerdictRolledBackPayload {
  verdict_id: string
  edit_id?: string
  actor?: string
  commit?: string | null
  toast?: string
}

/** `pair.control` payload (ADR 0023): the control replay's run id and what it showed. */
export interface PairControlPayload {
  golden: string
  replay: string
  control: string
  control_outcome: ControlOutcome
}

/** `pair.reblessed` payload; `drift_fact_ids` are the world facts archived with the old golden. */
export interface PairReblessedPayload {
  golden: string
  replay: string
  new_golden?: string
  archived?: string
  session_scoped?: boolean
  drift_fact_ids?: string[]
}

// ---------------------------------------------------------------- view models (CONTRACTS §7)
export interface Meta {
  public_mode: boolean
  simulate_enabled: boolean
  simulate_mode: string
  simulated_models: boolean
  action_token: string | null
  repo_url: string | null
  video_url: string | null
  bench_report_url: string | null
  version: string
  harness: string
  runner: string
  sandbox: string
}

export interface Scenario {
  scenario_id: string
  skill: string
  title: string
  learned: string
  catches: string
  expected_decision: Decision
  /** The global recorded run any visitor may open before pressing Simulate; null when none is seeded. */
  recorded_verdict_id?: string | null
}

export type SimulateStatus = 'running' | 'queued' | 'done'

export interface SimulateResponse {
  session_hash: string
  scenario_id: string
  edit_id: string
  verdict_id: string
  status: SimulateStatus
  queue_position: number | null
  eta_s: number | null
}
// edit_id / verdict_id may be null while queued (SimulateStart in orchestrator-api-cli.md §11).
export type SimulateResponseLoose = Omit<SimulateResponse, 'edit_id' | 'verdict_id'> & { edit_id: string | null; verdict_id: string | null }

export interface Page<T> {
  items: T[]
  next_cursor: string | null
}

export interface VerdictSummary {
  verdict_id: string
  edit_id: string
  decision: Decision
  action: Action
  headline: string
  affected_skills: string[]
  usd_total: number
  created_at: ISODateTime
  n_pairs: number
  outcome_counts: Partial<Record<PairOutcome, number>>
  recorded: boolean
  simulated_models: boolean
}

/** Orchestrator states as written to `edits.state`. */
export type OrchestratorState =
  | 'detected'
  | 'selecting'
  | 'dispatching'
  | 'collecting'
  | 'judging'
  | 'drifting'
  | 'controlling'
  | 'deciding'
  | 'acting'
  | 'notifying'
  | 'done'
  | 'failed_infra'

export type ProgressStageStatus = 'pending' | 'active' | 'done' | 'skipped'

export interface ProgressStage {
  key: string
  label: string
  status: ProgressStageStatus
  /** "" when there is nothing to add (StageView.detail defaults to ""). */
  detail: string | null
}

export interface Progress {
  stage: string
  stage_label: string
  k: number | null
  n: number | null
  elapsed_s: number
  usd_by_tier: TierMap<number>
  stalled: boolean
  stages: ProgressStage[]
}

export interface VerdictView {
  verdict_id: string
  state: OrchestratorState | (string & {})
  verdict: Verdict | null
  edit: SkillEdit
  diff: string
  progress: Progress | null
  /**
   * Rollback / Approve responses only: the toast line (ratchet/copy.py
   * `action_toast`). Absent or null on reads and on older backends.
   */
  toast?: string | null
}

export interface PairView {
  verdict_id: string
  decision: Decision | null
  /** Served from a recorded run of the scenario (the "Recorded" state). */
  recorded: boolean
  pair: PairVerdict
  golden: Trace
  replay: Trace
}

export interface ReblessResponse {
  new_golden_run_id: string
  archived_run_id: string
  /** The toast line (ratchet/copy.py REBLESS_TOAST); older backends omit it. */
  toast?: string | null
}

export interface EditView {
  edit: SkillEdit
  diff: string
  verdict_id: string | null
  state: OrchestratorState | (string & {})
}

/** Not pinned by CONTRACTS §7; the shape the dashboard expects. */
export interface TraceSummary {
  run_id: string
  kind: Trace['kind']
  task_input: string
  task_label: string
  skills_invoked: string[]
  world_dependent: boolean
  n_facts: number
  last_replayed_at: ISODateTime | null
  blessed_at: ISODateTime | null
  created_at: ISODateTime
}

export interface CostTile {
  usd_by_tier: TierMap<number>
  tokens_by_tier: TierMap<number>
  /** Prompt tokens sent, by tier (older backends omit it). */
  prompt_tokens_by_tier?: TierMap<number>
  /** Of those, the prompt tokens served from the provider's prompt cache. */
  cached_prompt_tokens_by_tier?: TierMap<number>
  /** Cached / total prompt tokens over every tier, 0..1; null when nothing was recorded. */
  cache_share?: number | null
}

/** `GET /costs?from=&to=`: totals for `[from, to)` (ISO 8601 with offset). */
export interface CostWindow extends CostTile {
  from: ISODateTime
  to: ISODateTime
}

/**
 * The savings line: judge spend over every verdict in the window (the last 7 days, or
 * `from`/`to`), the tiles' population, vs the same judge tokens priced on Ultra.
 */
export interface Counterfactual {
  actual_usd: number
  /** The same `actual_usd`, split by tier (costs are always shown with their tier). */
  actual_usd_by_tier?: TierMap<number>
  all_ultra_usd: number
  saved_ratio: number
  /** How many verdicts the window holds. */
  n_verdicts: number
}

/**
 * Without `from`/`to`: `by_purpose` is the last 7 days and `by_day` the last 14. With them,
 * both are filtered to the window and `window` carries its totals; otherwise `window` is null.
 */
export interface CostsView {
  today: CostTile
  week: CostTile
  last_edit: (CostTile & { edit_id: string | null; verdict_id: string | null; counterfactual_usd: number }) | null
  by_purpose: { purpose: Purpose; tier: Tier; calls: number; usd: number; tokens: number }[]
  by_day: { day: string; usd_by_tier: TierMap<number> }[]
  counterfactual: Counterfactual
  bench_report_url: string | null
  window?: CostWindow | null
}

export interface DigestItem {
  edit_id: string
  verdict_id: string | null
  skill: string
  summary: string
}

/**
 * `merged` / `blocked` are `list[str]` (verdict ids) in the orchestrator's Digest
 * (docs/design/orchestrator-api-cli.md §11); richer items are accepted too.
 */
export interface Digest {
  digest_id: string
  period_start: ISODateTime
  period_end: ISODateTime
  narrative: string
  verdict_ids: string[]
  merged: (string | DigestItem)[]
  blocked: (string | DigestItem)[]
  usd?: number
  simulated_models?: boolean
  /** Product metrics for the digest's week, as `ratchet metrics` prints them. */
  metrics?: MetricRow[]
}

/** One product-metrics row (`MetricRow`): both columns are display strings. */
export interface MetricRow {
  metric: string
  value: string
}

/** `GET /metrics/product?since=7d` and `GET /metrics/demo` (operator host only). */
export interface MetricsView {
  since: ISODateTime
  rows: MetricRow[]
}

/** `GET /edits/{id}/logs` (operator host only): batch `worker.log` tails plus crash info. */
export interface LogsView {
  lines: string[]
  /** True when the server cut the tail to its last 500 lines. */
  truncated: boolean
}

/** One row of `GET /runs?unblessed=1` (operator host only). */
export interface RunView {
  run_id: string
  source: 'captured' | 'live' | 'golden'
  task_preview: string
  task_label: string
  agent_tier: Tier | (string & {})
  n_steps: number
  last_activity_at: ISODateTime
  blessed: boolean
}

export interface RunsView {
  items: RunView[]
}

export interface Ticker {
  usd_by_tier: TierMap<number>
  total_usd: number
  live: boolean
}

/** Read-only settings view (operator host only). Not pinned by CONTRACTS §7. */
export interface SettingsView {
  caps: { purpose: string; usd_per_day: number }[]
  runner: string
  harness: string
  sandbox: string
  webhook_configured: boolean
  models: Record<Tier, string>
}

export type DemoEventName =
  | 'demo.landing_view'
  | 'demo.simulate_click'
  | 'demo.verdict_view'
  | 'demo.evidence_open'
  | 'demo.action_click'
  | 'demo.repo_click'
  | 'demo.video_click'

/** Body of POST /demo/events (DemoEventIn). Props values are strings or integers only. */
export interface DemoEvent {
  kind: DemoEventName
  session_hash: string
  ts: ISODateTime
  device: 'mobile' | 'tablet' | 'desktop'
  referrer: 'devpost' | 'youtube' | 'github' | 'other' | 'none'
  props: Record<string, string | number>
}

export interface ApiErrorBody {
  error: { code: string; message: string; request_id: string; details?: unknown }
}
