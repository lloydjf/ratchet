/**
 * Every user-facing string (landing, privacy note and digest copy included), in one
 * place. Mirrors `ratchet/copy.py` so the UI, CLI, notifications and narrative share one
 * voice. Voice: sentences under 20 words, active, no exclamation marks, no emoji, costs
 * always with their tier.
 */
import type { Decision, OpinionVerdict, PairOutcome, SideEffectClass, Tier } from './api/types'

// ---------------------------------------------------------------- product
export const PRODUCT = 'Ratchet'
export const TAGLINE = 'Regression CI for self-improving agents'

/** Landing headline. */
export const LANDING_HEADLINE =
  'Your agent learns new skills every day. Ratchet makes sure it never unlearns the old ones.'

/** Judge-facing landing copy, verbatim (≤ 90 words). */
export const LANDING_COPY =
  'Ratchet is regression CI for self-improving agents. When a Hermes or OpenClaw agent edits one of its own skills, Ratchet replays your blessed tasks in sandboxes on Nebius Serverless Jobs, judges them with a Nemotron cascade (Nano screens, Super checks, Ultra adjudicates), uses Tavily to tell world drift from regression, and blocks or reverts the edit if anything got worse. Press Simulate to watch it catch one.'

/** Footer privacy note on the public host. */
export const PRIVACY_NOTE =
  'This demo records anonymous page and button events to make sure it works for judges. No personal data is collected.'

// ---------------------------------------------------------------- tiers
export const TIER_NAME: Record<Tier, string> = {
  nano: 'Nemotron Nano',
  super: 'Nemotron Super',
  ultra: 'Nemotron Ultra',
}
export const TIER_SHORT: Record<Tier, string> = { nano: 'Nano', super: 'Super', ultra: 'Ultra' }
export const TIER_LETTER: Record<Tier, string> = { nano: 'N', super: 'S', ultra: 'U' }

/** "{Tier} judged {verdict} with {confidence}% confidence. {first reason}" */
export function tierTooltip(tier: Tier, verdict: OpinionVerdict, confidence: number, firstReason?: string): string {
  const pct = Math.round(confidence * 100)
  const base = `${TIER_NAME[tier]} judged ${verdict} with ${pct}% confidence.`
  return firstReason ? `${base} ${ensurePeriod(firstReason)}` : base
}
export const SKIPPED_TIER = 'Not needed: decided earlier in the cascade.'
export const TRUNCATED_MARKER = "Some evidence was shortened to fit this judge's context."

/** "Checked without a model: {n} new {class} call(s)." */
export function level0Marker(n: number, cls: SideEffectClass | string): string {
  return `Checked without a model: ${n} new ${cls} call${n === 1 ? '' : 's'}.`
}
export const LEVEL0_LABEL = { none: 'No action change', changed: 'Changed args', hard: 'Hard fail' } as const

/** ADR 0024: below this output similarity Nano may not clear a pair alone (cascade.OUTPUT_SIMILARITY_FLOOR). */
export const OUTPUT_SIMILARITY_FLOOR = 0.9

/** Pair page Level 0 section. */
export const LEVEL0 = {
  title: 'Level 0 · actions compared without a model',
  noActionChange: 'The replay made the same side-effect calls as the original run.',
  /** Shown when the decision did not rest on Nano although Nano screened first (ADR 0024). */
  nanoGated: 'Nano could not clear this pair alone (Level 0 found differences).',
}

/**
 * ADR 0024 `Level0Finding.output_similarity` (0..1; absent means the backend default 1.0):
 * "Final output: identical" at 0.999 or above, else "Final output: 87 % similar". The
 * percentage rounds down, so a pair under the 0.90 floor never reads as 90 %.
 */
export function outputSimilarityCopy(similarity: number | null | undefined): string {
  const s = similarity ?? 1
  if (s >= 0.999) return 'Final output: identical'
  return `Final output: ${Math.min(99, Math.max(0, Math.floor(s * 100)))} % similar`
}

// ---------------------------------------------------------------- decisions
export const DECISION_WORD: Record<Decision, string> = { PASS: 'PASS', WARN: 'WARN', BLOCK: 'BLOCK' }
export const DECISION_GLYPH: Record<Decision, string> = { PASS: '✓', WARN: '!', BLOCK: '✕' }
export const DECISION_ARIA: Record<Decision, string> = {
  PASS: 'Decision: PASS',
  WARN: 'Decision: WARN',
  BLOCK: 'Decision: BLOCK',
}

export const headline = {
  /** Blocked: the new {skill} would {action} in {n} of {m} replays. */
  blockLevel0: (skill: string, action: string, n: number, m: number) =>
    `Blocked: the new ${skill} would ${action} in ${n} of ${m} replays.`,
  /** Blocked: {skill} now {failure} on {n} of {m} past tasks. */
  blockCascade: (skill: string, failure: string, n: number, m: number) =>
    `Blocked: ${skill} now ${failure} on ${n} of ${m} past tasks.`,
  /** Held: {reason} on {n} of {m} replays. Review before merging. */
  warn: (reason: string, n: number, m: number) => `Held: ${reason} on ${n} of ${m} replays. Review before merging.`,
  /** Passed: {skill} still handles all {m} past tasks. */
  pass: (skill: string, m: number) => `Passed: ${skill} still handles all ${m} past tasks.`,
  /** Passed: {m} tasks checked; {n} changed because the world did, not the skill. */
  passDrift: (m: number, n: number) =>
    `Passed: ${m} tasks checked; ${n} changed because the world did, not the skill.`,
  /** Passed: no blessed tasks use {skill} yet. Bless one to protect it. */
  noTraces: (skill: string) => `Passed: no blessed tasks use ${skill} yet. Bless one to protect it.`,
}

export const NARRATIVE_ENDINGS = [
  'Nothing to do.',
  'Approve if this is intended.',
  'Roll back if this is not intended.',
  'Re-bless the changed tasks.',
] as const

/** Headline validation: starts with the decision word and names the skill. */
export function headlineMatchesTemplate(text: string, decision: Decision, skills: string[]): boolean {
  const word = decision === 'BLOCK' ? 'Blocked:' : decision === 'WARN' ? 'Held:' : 'Passed:'
  if (!text.startsWith(word)) return false
  const isDriftPass = /changed because the world did, not the skill\.$/.test(firstSentence(text) + '')
  return isDriftPass || skills.some((s) => text.includes(s))
}

// ---------------------------------------------------------------- pair outcomes
export const OUTCOME_LABEL: Record<PairOutcome, string> = {
  pass: 'Same result',
  regression: 'Got worse',
  drift: 'World changed',
  improved: 'Better',
  uncertain: 'Not sure',
  failed: 'Could not replay',
}
export const OUTCOME_GLYPH: Record<PairOutcome, string> = {
  pass: '✓',
  regression: '✕',
  drift: '~',
  improved: '↑',
  uncertain: '?',
  failed: '–',
}
export const outcomeExplanation = {
  pass: () => 'Output meets the criteria; differences are cosmetic.',
  regression: (criterion: string) => `${ensureNoPeriod(criterion)} is no longer met.`,
  drift: (fact: string) => `${ensureNoPeriod(fact)} changed since the original run; the new output follows from it.`,
  improved: (criterion: string) => `Meets the criteria and improves ${ensureNoPeriod(criterion)}.`,
  uncertain: (reason: string) => `${ensureNoPeriod(reason)}. Held for a human.`,
  failed: (codeExplanation: string) => `${ensureNoPeriod(codeExplanation)}.`,
}

export const OPINION_LABEL: Record<OpinionVerdict, string> = {
  equivalent: 'Equivalent',
  regressed: 'Regressed',
  improved: 'Improved',
  uncertain: 'Uncertain',
}
export const OPINION_GLYPH: Record<OpinionVerdict, string> = {
  equivalent: '✓',
  regressed: '✕',
  improved: '↑',
  uncertain: '?',
}

// ---------------------------------------------------------------- progress
/** Fixed stage label set (the progress stages the API reports). Keys are the Progress.stages[].key values. */
export const STAGE_KEYS = [
  'detected',
  'selecting',
  'replaying',
  'level0',
  'nano',
  'super',
  'ultra',
  'drift',
  // ADR 0023 control replays run after drifting, so Tavily resolves world-drift pairs first.
  'controls',
  'summary',
  'done',
] as const
export type StageKey = (typeof STAGE_KEYS)[number]

/** Where replays run, named honestly: "Nebius Jobs" only when the runner really is Nebius. */
export function runnerName(runner: string | null | undefined): string {
  if (!runner) return 'the replay workers'
  if (runner.startsWith('nebius')) return 'Nebius Jobs'
  if (runner.startsWith('local')) return 'the local runner'
  return 'the replay workers'
}

/** Landing "How it works" step: "Replay on Nebius Jobs" / "Replay on the local runner". */
export function replayStep(runner: string | null | undefined): string {
  return runner ? `Replay on ${runnerName(runner)}` : 'Replay in sandboxes'
}

let hostRunner: string | null = null

/** Called once /meta arrives so live progress names this host's runner. */
export function setHostRunner(runner: string | null | undefined): void {
  hostRunner = runner ?? null
}

export function stageLabel(
  key: string,
  k?: number | null,
  n?: number | null,
  runner: string | null = hostRunner,
): string {
  const kn = k != null && n != null ? `${k}/${n}` : n != null ? `${n}` : null
  const where = runnerName(runner)
  switch (key) {
    case 'detected':
      return 'Detected edit'
    case 'selecting':
      return n != null ? `Choosing tasks (${n})` : 'Choosing tasks'
    case 'replaying':
      return kn ? `Replaying on ${where} (${kn})` : `Replaying on ${where}`
    case 'level0':
      return 'Checking actions'
    case 'nano':
      return n != null ? `Nano screening (${n})` : 'Nano screening'
    case 'super':
      return n != null ? `Super checking (${n})` : 'Super checking'
    case 'ultra':
      return n != null ? `Ultra deciding (${n})` : 'Ultra deciding'
    // ADR 0023: k/n here count control batches (`ctrl_…`), never the edit's own replays.
    case 'controls':
    case 'controlling':
      return k != null && n != null ? `Replaying controls (${k}/${n})` : 'Replaying controls'
    case 'drift':
      return n != null ? `Asking Tavily (${n} fact${n === 1 ? '' : 's'})` : 'Asking Tavily'
    case 'summary':
    case 'narrative':
      return 'Writing the summary'
    case 'done':
      return 'Done'
    // Orchestrator states (`edits.state`), used by Verdict.stages StageTiming rows.
    case 'dispatching':
      return `Sending to ${where}`
    case 'collecting':
      return kn ? `Replaying on ${where} (${kn})` : `Replaying on ${where}`
    case 'judging':
      return 'Judging (Nano, Super, Ultra)'
    case 'drifting':
      return n != null ? `Asking Tavily (${n} fact${n === 1 ? '' : 's'})` : 'Asking Tavily'
    case 'deciding':
      return 'Writing the summary'
    case 'acting':
      return 'Acting on the decision'
    case 'notifying':
      return 'Notifying'
    case 'failed_infra':
      return 'Replays failed'
    default:
      return key
  }
}

/** Mobile short labels ("Replaying 9/14"). */
export function stageLabelShort(key: string, k?: number | null, n?: number | null): string {
  const kn = k != null && n != null ? ` ${k}/${n}` : ''
  switch (key) {
    case 'detected':
      return 'Edit found'
    case 'selecting':
      return n != null ? `Tasks (${n})` : 'Tasks'
    case 'replaying':
      return `Replaying${kn}`
    case 'level0':
      return 'Actions'
    case 'nano':
      return 'Nano'
    case 'super':
      return 'Super'
    case 'ultra':
      return 'Ultra'
    case 'controls':
    case 'controlling':
      return `Controls${kn}`
    case 'drift':
      return 'Tavily'
    case 'summary':
    case 'narrative':
      return 'Summary'
    case 'done':
      return 'Done'
    default:
      return key
  }
}

export const stalledCopy = (stage: string) => `Still working — ${stage} is taking longer than usual.`
export const STALL_AFTER_S = 20

// ---------------------------------------------------------------- control replays (ADR 0023)
/** ratchet/copy.py `explain_nondeterminism`: the server's `pair.reason` for a control-held pair. */
export const NONDETERMINISM_REASON =
  'The unchanged skill also produced a different result on this task, so the difference is nondeterminism, not the edit.'
export const CONTROL = {
  agrees: 'Control: agrees',
  diverges: 'Control: diverges',
  agreesHint: 'Control replay: the unchanged skill reproduced the original result, so the difference comes from the edit.',
  divergesHint: 'Control replay: the unchanged skill also produced a different result, so the difference is counted as nondeterminism, not a regression.',
  /** Visible qualifier next to the outcome chip of a pair a diverging control decided. */
  noisy: '(noisy task)',
  /** Judge-trail "C" badge for `decided_by: "control"`. */
  trailTip: 'Control replay: the unchanged skill also produced a different result',
  sectionTitle: 'Control run',
  sectionIntro: 'Ratchet replayed this task once more with the unchanged skill (the parent commit) and judged it against the original.',
  run: 'Control',
  openTrace: 'Open the control trace',
  /** Verdict side panel, Evidence quality. */
  evidenceLabel: 'Control replays',
  evidenceValue: (n: number, m: number) => `${n} (${m} diverged)`,
  evidenceHint: 'Contested pairs replayed with the unchanged skill to tell nondeterminism from regression.',
  /** Timeline row for `pair.control`. */
  timeline: (outcome: string | null | undefined, task: string) =>
    outcome === 'agrees'
      ? `Control replay agreed for ${task}`
      : outcome === 'diverges'
        ? `Control replay diverged for ${task} — counted as nondeterminism`
        : `Control replay finished for ${task}`,
  timelineSummary: (outcome: string | null | undefined) =>
    outcome === 'agrees'
      ? 'The unchanged skill matched the original, so the judged outcome stands.'
      : outcome === 'diverges'
        ? 'The unchanged skill also differed, so the difference is not the edit.'
        : 'The control was inconclusive; the pair stays as judged.',
}

// ---------------------------------------------------------------- actions
export type ActionKind = 'rollback' | 'approve' | 'approveAnyway' | 'rebless' | 'unbless' | 'bless'
export const ACTION_LABEL: Record<ActionKind, string> = {
  rollback: 'Rollback',
  approve: 'Approve',
  approveAnyway: 'Approve anyway',
  rebless: 'Re-bless',
  unbless: 'Unbless',
  bless: 'Bless',
}
/** The confirm button inside the dialog names the exact git action. */
export const CONFIRM_LABEL: Record<ActionKind, string> = {
  rollback: 'Roll back',
  approve: 'Approve and merge',
  approveAnyway: 'Re-apply and merge',
  rebless: 'Re-bless',
  unbless: 'Unbless',
  bless: 'Bless',
}
export const confirmBody = {
  rollback: (sha: string) =>
    `This reverts commit ${sha} on candidate and leaves main unchanged. You can re-apply it later from the edit page.`,
  approve: (skill: string) =>
    `This merges candidate into main. Your agent will use the new ${skill} on its next task. Rollback stays available.`,
  approveAnyway: (skill: string) =>
    `This re-applies the reverted edit to ${skill} and merges it into main, overriding the block. Your agent will use it on its next task. Rollback stays available.`,
  rebless: (task: string) =>
    `This makes the replay the new reference for "${task}" and archives the old one with the reason "world changed".`,
  unbless: (task: string) =>
    `Ratchet stops replaying "${task}" when its skills change. The run and its steps are kept.`,
  bless: (task: string) =>
    `This makes the run the reference for "${task}". Ratchet replays it whenever a skill it uses changes.`,
}
export const confirmTitle: Record<ActionKind, string> = {
  rollback: 'Roll back this edit?',
  approve: 'Approve this edit?',
  approveAnyway: 'Approve this blocked edit?',
  rebless: 'Re-bless this task?',
  unbless: 'Unbless this task?',
  bless: 'Bless this run?',
}
/** Bless toast. ratchet/copy.py has no BLESS_TOAST yet, and `POST /runs/{id}/bless` returns a bare Trace. */
export const BLESS_TOAST = 'Blessed. Future edits to its skills are now checked against this run.'
/** Client fallbacks for the server's `toast` field (ratchet/copy.py); the event's text wins when present. */
export const successToast = {
  rollback: () => 'Rolled back. main is unchanged.',
  /** Rolling back an edit that was already merged takes it out of main. */
  rollbackMerged: () => 'Rolled back. main no longer has this edit.',
  approve: (skill: string) => `Merged. ${skill} is live.`,
  approveAnyway: (skill: string) => `Approved anyway. ${skill} is re-applied and live on main.`,
  rebless: () => "Re-blessed. Future checks compare against today's result.",
  unbless: () => 'Unblessed. Ratchet no longer replays this task.',
  bless: () => BLESS_TOAST,
  criteria: () => 'Success criteria saved. The next replay uses them.',
}
export const undoHint: Record<ActionKind, string> = {
  rollback: 'Undo: re-apply from the edit page.',
  approve: 'Undo: Rollback stays available.',
  approveAnyway: 'Undo: Rollback stays available.',
  rebless: 'Undo: the old reference is archived, not deleted.',
  unbless: 'Undo: bless it again from the trace page.',
  bless: 'Undo: unbless it from the trace page.',
}
export const CANCEL = 'Cancel'
export const WORKING = 'Working…'
export const rolledBackBy = (actor: string, time: string) => `Rolled back by ${actor} at ${time}`
export const approvedAt = (time: string) => `Approved at ${time}`
export const ACTED_COPY: Record<'merged' | 'held' | 'reverted', string> = {
  merged: 'Merged to main',
  held: 'Held on candidate',
  reverted: 'Reverted on candidate',
}
export const MAIN_UNCHANGED = 'main is unchanged'
export const OPEN_EDIT = 'Open the edit'
export const REAPPLY_FROM_EDIT = 're-apply from the edit page'

// ---------------------------------------------------------------- verdict page
export const VERDICT = {
  queued: 'Queued. Your run starts as soon as a slot is free.',
  checking: 'Checking the edit to',
  checkingBody: 'Ratchet is replaying blessed tasks against the new skill. The decision appears here when the judges finish.',
  writtenBy: (tier: string) => `Written by ${tier} from the evidence below.`,
  writtenFromAbove: (tier: string) => `Written by ${tier} from the evidence above.`,
  summary: 'Summary',
  details: 'Cost and run details',
  whatChanged: 'What changed: ',
  editPage: 'Edit page',
  runningCost: 'Running cost',
  judgesCost: 'Judges cost',
  skill: 'Skill',
  skills: 'Skills',
  editBy: 'Edit by',
  seeded: 'seeded scenario',
  decided: 'Decided',
  took: 'Took',
  commit: 'commit',
  revertCommit: 'revert commit',
  approved: 'approved',
  approvedAnyway: 'approved anyway',
  replayed: 'Replayed tasks',
  diff: 'Skill diff',
  drift: 'World drift',
  driftSource: 'Checked with Tavily',
  reblessIntro: 'Re-bless makes each replay the new reference.',
  reblessed: 'Re-blessed',
  stagesDone: (n: string) => `${n} done`,
}

// ---------------------------------------------------------------- traces
export const TRACE = {
  title: 'Trace',
  kind: { golden: 'Golden', replay: 'Replay', live: 'Live run' } as Record<string, string>,
  archived: (reason: string) => `Archived: ${reason}`,
  bless: 'Bless',
  blessing: 'Blessing…',
  editCriteria: 'Edit criteria',
  saveCriteria: 'Save criteria',
  saving: 'Saving…',
  criteriaHint: 'One criterion per line',
}
export const TRACES = {
  title: 'Golden library',
  liveTitle: 'Recent runs',
  intro: 'Blessed tasks Ratchet replays when a skill they use changes.',
  liveIntro: 'Runs your agent finished recently. Bless one to protect it.',
  search: 'Search tasks',
  noMatch: 'No tasks match this search.',
  clearSearch: 'Clear the search',
  showRuns: 'Show recent runs',
  showBlessed: 'Show blessed tasks',
  noRuns: 'No finished runs yet. Run a task with your agent, then come back to bless it.',
  source: { captured: 'Captured', live: 'Live run', golden: 'Golden' } as Record<string, string>,
  blessed: 'Blessed',
  openTrace: 'Open the trace',
  steps: (n: number) => `${n} step${n === 1 ? '' : 's'}`,
}

// ---------------------------------------------------------------- errors (by code; generic fallbacks)
const ERRORS: Record<string, string> = {
  upstream_unavailable:
    "The model service didn't respond. Ratchet judged this pair as not sure and will keep the edit held.",
  judge_timeout: 'The final judge timed out. The edit is held for you to review.',
  drift_unavailable: "Couldn't check whether the world changed. Treating this difference as a possible regression.",
  budget_exceeded: 'Spending cap reached for today. The edit is held; raise RATCHET_DAILY_USD_CAP in .env or wait until tomorrow.',
  merge_conflict: 'The skill files conflict with main. Resolve the conflict in the skills repo, then approve.',
  not_public: 'Blessing and recording are off on the demo. Run Ratchet locally to protect your own agent.',
  tier_mismatch: 'Replay refused: model tier differs from the original run.',
  schema_failure: 'Judge answer unreadable; treated as uncertain.',
  sandbox_failed: 'Sandbox error on this task.',
  harness_failed: 'Agent crashed during replay.',
  harness_mismatch: 'Cannot replay: different agent runtime.',
  no_affected_traces: 'Passed: no blessed tasks use this skill yet.',
  ratchet_blocked: 'blocked action',
  action_locked: 'Someone else is acting on this verdict.',
  rate_limited: 'Too many actions in a minute. Wait a moment and try again.',
  simulate_busy: 'Many judges are running scenarios right now. Try again in a minute, or view a recorded run.',
  action_token_invalid: 'This page is out of date. Reload and try again.',
  already_reverted: 'This edit is already reverted; main is unchanged.',
  stacked_on_held: 'An earlier edit on candidate is still held. Approve or roll it back first; merging this one would ship it too.',
  not_ready: 'Ratchet is still acting on this verdict. Try again in a few seconds.',
  worker_lost: 'The replay worker stopped before this task ran. The edit is held. Try again from the edit page.',
  not_owner: 'Actions are available on your own Simulate runs. Press Simulate to start one.',
  not_found: 'This page does not exist, or the run it points to has been reset.',
  network: "Ratchet's server didn't respond. Check your connection, then try again.",
  simulate_disabled: 'Simulate is off on this host. Recorded runs are on the timeline.',
  payload_too_large: 'That request is too large. Ratchet only accepts small JSON bodies.',
}

/** Per-client variant of `simulate_busy` (ratchet/copy_errors.py SIMULATE_BUSY_CLIENT). */
export const SIMULATE_BUSY_CLIENT = 'You already have a scenario running. Wait for it to finish, then try another.'

/**
 * The API's 503 `not_ready`: a component the route needs is not wired yet
 * (ratchet/copy_errors.py NOT_READY_HOST). Same code as the 409 action refusal, other text.
 */
export const NOT_READY_HOST = 'This host is not fully started yet; try again in a moment.'

/** Seconds a refused action (409 `not_ready`) stays disabled before it can be tried again. */
export const NOT_READY_RETRY_MS = 3000

/**
 * Copy for a failed API call. 429s carry their own server sentence (per-client simulate busy,
 * action rate limit), so the server's message wins there; so does a 503 `not_ready` (the
 * host is still starting), falling back to NOT_READY_HOST. Everything else maps by code.
 */
export function apiErrorCopy(e: { code?: string; status?: number; message?: string } | null | undefined): string {
  const code = e?.code ?? 'network'
  const msg = e?.message?.trim()
  const sentence = !!msg && msg !== code && /[.?]$/.test(msg)
  if (e?.status === 429 && sentence) return msg!
  if (e?.status === 503 && code === 'not_ready') return sentence ? msg! : NOT_READY_HOST
  return errorCopy(code)
}
/** `replay_infra` takes counts: "Replays failed on {n} of {m} tasks…" */
export const replayInfra = (n: number, m: number) =>
  `Replays failed on ${n} of ${m} tasks, so Ratchet can't decide. The edit is held. Try again from the edit page.`

export function errorCopy(code: string | undefined | null, fallback?: string): string {
  if (code === 'replay_infra') return "Replays failed, so Ratchet can't decide. The edit is held. Try again from the edit page."
  if (code && ERRORS[code]) return ERRORS[code]
  return fallback ?? 'Something went wrong on our side. Ratchet kept everything as it was. Try again.'
}
export const RETRY = 'Try again'
export const VIEW_RECORDED = 'View a recorded run'

// ---------------------------------------------------------------- empty states
export const EMPTY = {
  timeline: 'Nothing here yet. Bless a finished task or simulate a scenario.',
  verdicts: 'No skill edits checked yet. When your agent changes a skill, the verdict appears here.',
  traces: 'No blessed tasks. Run a task, then press Bless on it.',
  costs: 'No spend yet.',
  digest: 'No digest yet. The first one is written at the end of the week.',
  digestAction: 'Open the timeline',
  pairs: 'No tasks were replayed for this edit.',
  filtered: 'No events match these filters.',
}

// ---------------------------------------------------------------- badges
export const BADGE = {
  recorded: 'Recorded run',
  recordedHint: 'Served from a recorded run of this scenario, not computed live.',
  truncated: 'Truncated',
  driftUnavailable: 'Drift check unavailable',
  simulated: 'Simulated models',
  simulatedHint:
    "Produced by Ratchet's offline simulator, not Nemotron. Set NEBIUS_TOKEN_FACTORY_KEY to use real models.",
  blocked: 'Blocked action',
  worldDependent: 'World-dependent',
}

// ---------------------------------------------------------------- scenario card
export const SCENARIO = {
  simulate: 'Simulate',
  running: 'Running',
  openVerdict: 'Open verdict',
  queued: (n: number, eta: number) =>
    `${n} judge${n === 1 ? ' is' : 's are'} running scenarios; yours starts in ~${Math.max(1, Math.round(eta))} s`,
  learned: 'What the agent learned',
  catches: 'What Ratchet should catch',
  expected: 'Expected',
  failed: "This run didn't finish. The scenario was reset; nothing changed.",
}

// ---------------------------------------------------------------- misc
// ---------------------------------------------------------------- live feed
export const LIVE_LIMITED = 'Live updates paused (too many open tabs)'
export const LIVE_LIMITED_HINT = 'This page checks for new events every few seconds instead. Close other Ratchet tabs to resume live updates.'

// ---------------------------------------------------------------- debug drawer
export const DEBUG = {
  title: 'Debug',
  verdict: 'Verdict',
  edit: 'Edit',
  runner: 'Runner',
  batches: 'Batches',
  /** Public host (or a backend without `GET /edits/{id}/logs`): the CLI prints the same lines. */
  logsCli: (editId: string) => `ratchet logs --edit ${editId}`,
  logsLabel: 'Worker logs:',
  logsNote: 'Run this on the Ratchet host to print each batch’s worker.log.',
  loading: 'Loading worker logs…',
  noLines: 'No log lines for this edit yet.',
  /** `LogsView.truncated`: the server kept only the tail. */
  truncated: '(last 500 lines)',
}

// ---------------------------------------------------------------- digest
export const DIGEST = {
  metricsTitle: 'Product metrics',
  metric: 'Metric',
  value: 'Value',
}

// ---------------------------------------------------------------- prompt cache (Costs page)
export const CACHE_SHARE = {
  label: 'Prompt-cache share',
  hint: 'The part of the prompt tokens the provider served from its prompt cache instead of reading them again.',
}

/** A share as the CLI prints it (ratchet/cli/report.py `_share`): "0.41", or "n/a" with no prompt tokens. */
function shareText(cached: number, prompt: number): string {
  return prompt > 0 ? (cached / prompt).toFixed(2) : 'n/a'
}

/**
 * Costs tile line: "Prompt-cache share: 0.41 of prompt tokens (nano 0.52 · super 0.38 ·
 * ultra 0.12)", or "Prompt-cache share: not recorded" when `cache_share` is null or absent.
 */
export function cacheShareLine(tile: {
  cache_share?: number | null
  prompt_tokens_by_tier?: Partial<Record<Tier, number>>
  cached_prompt_tokens_by_tier?: Partial<Record<Tier, number>>
}): string {
  if (tile.cache_share == null) return `${CACHE_SHARE.label}: not recorded`
  const prompt = tile.prompt_tokens_by_tier ?? {}
  const cached = tile.cached_prompt_tokens_by_tier ?? {}
  const tiers = (['nano', 'super', 'ultra'] as const).map((t) => `${t} ${shareText(cached[t] ?? 0, prompt[t] ?? 0)}`)
  return `${CACHE_SHARE.label}: ${tile.cache_share.toFixed(2)} of prompt tokens (${tiers.join(' · ')})`
}

export const COST_SAVED = (usd: string, ratio: string) => `cascade saved ${usd} (${ratio}×)`
/** Costs page counterfactual line: the window's verdict count (`Counterfactual.n_verdicts`). */
export const overVerdicts = (n: number) => `over ${n} verdict${n === 1 ? '' : 's'} in this window`
export const SHOW_LOGS = 'Show logs'
export const HIDE_LOGS = 'Hide logs'
export const JUMP_TO_DIFF = 'Jump to first difference'
export const DIFFERENCES_ONLY = 'Differences only'

export function pageTitle(parts: (string | null | undefined)[]): string {
  return [...parts.filter(Boolean), PRODUCT].join(' — ')
}

// ---------------------------------------------------------------- helpers
export function firstSentence(text: string): string {
  const m = text.match(/^.*?[.?](?=\s|$)/s)
  return (m ? m[0] : text).trim()
}
function ensurePeriod(s: string): string {
  const t = s.trim()
  return /[.?]$/.test(t) ? t : `${t}.`
}
function ensureNoPeriod(s: string): string {
  return s.trim().replace(/[.]$/, '')
}

// ---------------------------------------------------------------- decision band
/** The display word: one per decision, the only text set at display size. */
export const DECISION_DISPLAY: Record<Decision, string> = { BLOCK: 'Blocked.', WARN: 'Held.', PASS: 'Passed.' }
/** The headline's decision prefix ("Blocked: …"), which the band shows as the display word instead. */
/** Decision chips (timeline, verdict list, scenario cards): the headline words, sentence case. */
export const DECISION_CHIP: Record<Decision, string> = { BLOCK: 'Blocked', WARN: 'Held', PASS: 'Passed' }
export const HEADLINE_PREFIX: Record<Decision, string> = { BLOCK: 'Blocked:', WARN: 'Held:', PASS: 'Passed:' }

// ---------------------------------------------------------------- inline confirm
/** Button words. The button, its confirm and its toast share one verb ("Roll back" → "Rolled back."). */
export const ACTION_VERB: Record<ActionKind, string> = {
  rollback: 'Roll back',
  approve: 'Approve',
  approveAnyway: 'Approve anyway',
  rebless: 'Re-bless',
  unbless: 'Unbless',
  bless: 'Bless',
}
/** The exact git operation each verdict action runs. */
export const gitAction = {
  rollback: (sha: string, merged: boolean) => (merged ? [`git revert ${sha}`, 'on candidate, then main'] : [`git revert ${sha}`, 'on candidate']),
  approve: () => ['git merge candidate', 'into main'],
  approveAnyway: (revertSha: string) => [`git revert ${revertSha} && git merge candidate`, 'into main'],
}
export const CONFIRM_STEP = 'Confirm'

// ---------------------------------------------------------------- cascade sieve
export const SIEVE = {
  title: 'Where each task was decided',
  entry: 'Replayed',
  columns: { level0: 'Level 0', nano: 'Nano', super: 'Super', ultra: 'Ultra' } as Record<string, string>,
  rail: 'Decided',
  reached: (n: number) => `${n} reached`,
  noModel: 'no model',
  judging: 'Being judged',
  waiting: 'Waiting for Level 0',
  decidedAt: {
    level0: 'Level 0, without a model',
    nano: 'Nemotron Nano',
    super: 'Nemotron Super',
    ultra: 'Nemotron Ultra',
    drift: 'Tavily drift check',
    control: 'Control replay',
    infra: 'Never replayed',
  } as Record<string, string>,
  summary: (n: number, decided: number) => `${decided} of ${n} task${n === 1 ? '' : 's'} decided.`,
  keyboardHint: 'Arrow keys move between tasks; Enter opens one.',
  open: 'Open the pair',
}

// ---------------------------------------------------------------- landing hero
export const HERO = {
  /** One sentence above the recorded verdict; every number comes from that verdict. */
  sentence: (n: number, skill: string, decision: Decision) =>
    `A recorded run: Ratchet replayed ${n} blessed task${n === 1 ? '' : 's'} against an edit to ${skill} and ${decision === 'BLOCK' ? 'blocked it' : decision === 'WARN' ? 'held it for review' : 'let it through'}.`,
  fallback: 'Ratchet replays the tasks you blessed whenever your agent edits a skill, and blocks the edit if anything got worse.',
  run: 'Run this live',
  openFull: 'Open the full verdict',
  scenariosTitle: 'Three skill edits to try',
  scenariosNote: 'Each run takes under a minute and is yours alone.',
}

// ---------------------------------------------------------------- evidence linking
export const EVIDENCE = {
  citedBy: 'Cited by',
  hunkPairs: (n: number) => (n === 0 ? 'No pair traces to this hunk' : `${n} pair${n === 1 ? '' : 's'}`),
  hunkFilter: (n: number) => `Show the ${n} pair${n === 1 ? '' : 's'} this hunk affected`,
  filtered: (n: number, m: number) => `Showing ${n} of ${m} pairs affected by one hunk.`,
  clearFilter: 'Show all pairs',
}

// ---------------------------------------------------------------- shell
export const SHELL = {
  menu: 'Menu',
  closeMenu: 'Close menu',
  search: 'Jump to…',
  collapse: 'Collapse',
}
export const TICKER = {
  label: 'Spend today',
  allUltra: 'If Ultra judged everything',
  estimate: 'estimate',
  live: 'live',
}
export const PALETTE = {
  title: 'Command palette',
  placeholder: 'Jump to a verdict, trace or skill, or run a scenario…',
  empty: 'Nothing matches. Try a skill name or a decision.',
  groups: { go: 'Go to', verdicts: 'Verdicts', scenarios: 'Run a scenario', timeline: 'Filter the timeline', traces: 'Traces', skills: 'Skills', actions: 'Actions' } as Record<string, string>,
}
export const SHORTCUTS: [string[], string][] = [
  [['⌘', 'K'], 'Open the command palette (Ctrl K elsewhere)'],
  [['j'], 'Next pair'],
  [['k'], 'Previous pair'],
  [['o'], 'Open the selected pair'],
  [['r'], 'Roll back this edit'],
  [['a'], 'Approve this edit'],
  [['?'], 'Show these shortcuts'],
  [['Esc'], 'Close or cancel'],
]
export const SHORTCUTS_TITLE = 'Keyboard shortcuts'
