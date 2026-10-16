/**
 * In-memory fake of the Ratchet API for development before the backend exists
 * (`VITE_FIXTURES=1`). Same shapes as docs/CONTRACTS.md §7. Simulate runs stream progress
 * through ~20 s of stages and emit the same events the SSE feed would.
 */
import { ApiError, type RatchetApi, type StreamHandlers } from '../client'
import { stageLabel, STAGE_KEYS, successToast, type StageKey } from '../../copy'
import type {
  CostsView,
  Digest,
  EditView,
  Event,
  Meta,
  Page,
  Progress,
  ProgressStage,
  RunView,
  SettingsView,
  SimulateResponse,
  Tier,
  TierMap,
  Trace,
  TraceSummary,
  Verdict,
  VerdictSummary,
  VerdictView,
} from '../types'
import { hex, price } from './build'
import { buildChangelogPass, buildExpense, buildFx, buildReleaseWarn, buildTriage, controlBatchId, SCENARIOS, type BuildOpts, type Built } from './scenarios'

const HOUR = 3_600_000
/** Fixture prompt-cache hit rates by tier (the Costs page's prompt-cache share line). */
const FIXTURE_CACHE_RATE: Record<Tier, number> = { nano: 0.52, super: 0.38, ultra: 0.12 }
const SESSION = 'sess_' + hex(String(Date.now()), 10)

type Builder = (o: BuildOpts) => Built
const BUILDERS: Record<string, Builder> = {
  'github-triage': buildTriage,
  'expense-report': buildExpense,
  'fx-expense-converter': buildFx,
}

/** Seconds per stage for a simulated run (sums to ~20 s). 0 = skipped. */
const PLANS: Record<string, Partial<Record<StageKey, number>>> = {
  'github-triage': { detected: 1, selecting: 1.5, replaying: 8.5, level0: 1.5, nano: 3, super: 1.5, summary: 2.5 },
  'expense-report': { detected: 1, selecting: 1.5, replaying: 6, level0: 1, nano: 2.5, super: 2.5, ultra: 2.5, controls: 2.5, summary: 2 },
  'fx-expense-converter': { detected: 1, selecting: 1.2, replaying: 6, level0: 1, nano: 2, super: 2, ultra: 2, drift: 2.8, summary: 2 },
}

interface Run {
  scenarioId: string
  verdictId: string
  editId: string
  startAt: number | null
  queuedAt: number
  built: Built
  lastStage: string | null
  lastK: number
  /** Control replays reported so far (ADR 0023 `pair.control` events emitted). */
  lastCK: number
  finished: boolean
}

function speed(): number {
  const w = typeof window !== 'undefined' ? (window as unknown as { __RATCHET_FIXTURE_SPEED__?: number }) : undefined
  const fromWindow = w?.__RATCHET_FIXTURE_SPEED__
  const fromEnv = Number(import.meta.env.VITE_FIXTURE_SPEED ?? '')
  return fromWindow ?? (Number.isFinite(fromEnv) && fromEnv > 0 ? fromEnv : 1)
}

function summarize(v: Verdict): VerdictSummary {
  const counts: VerdictSummary['outcome_counts'] = {}
  for (const p of v.pairs) counts[p.outcome] = (counts[p.outcome] ?? 0) + 1
  return {
    verdict_id: v.verdict_id,
    edit_id: v.edit_id,
    decision: v.decision,
    action: v.action,
    headline: v.headline,
    affected_skills: v.affected_skills,
    usd_total: Object.values(v.cost).reduce((a, b) => a + (b ?? 0), 0),
    created_at: v.created_at,
    n_pairs: v.pairs.length,
    outcome_counts: counts,
    recorded: v.recorded,
    simulated_models: v.simulated_models,
  }
}

const delay = <T>(value: T, ms = 120): Promise<T> =>
  new Promise((resolve) => setTimeout(() => resolve(structuredClone(value)), ms))

export function createFixtureApi(): RatchetApi {
  const now = Date.now()
  const builts = new Map<string, Built>()
  const order: string[] = []
  const events: Event[] = []
  const listeners = new Set<StreamHandlers>()
  const runs = new Map<string, Run>()
  const reblessed = new Set<string>()
  /** Decided verdicts still in `acting` (Rollback / Approve answer 409 not_ready until then). */
  const actingUntil = new Map<string, number>()
  let nextEventId = 1

  function emit(kind: string, ref: string, payload: Record<string, unknown>, ts = Date.now(), session: string | null = null) {
    const e: Event = { id: nextEventId++, ts: new Date(ts).toISOString(), kind, ref, payload, session_hash: session }
    events.push(e)
    for (const l of listeners) l.onEvent(structuredClone(e))
    return e
  }

  function add(b: Built) {
    builts.set(b.verdict.verdict_id, b)
    order.unshift(b.verdict.verdict_id)
  }

  // ---------------------------------------------------------------- history
  const history: [Builder, BuildOpts, ((b: Built) => void)?][] = [
    [buildChangelogPass, { verdictId: 'v_9a41c2', editId: 'e_9a41c2', at: now - 5 * 24 * HOUR - 3 * HOUR, durationS: 41 }, (b) => {
      b.verdict.action = 'reverted' // a rollback leaves the verdict reverted (actions.py)
      b.verdict.rolled_back_by = 'jun'
      b.verdict.rolled_back_at = new Date(now - 5 * 24 * HOUR + 2 * HOUR).toISOString()
      b.verdict.action_commit = hex('revert-9a41c2', 40)
    }],
    [buildFx, { verdictId: 'v_rec_fx', editId: 'e_rec_fx', at: now - 3 * 24 * HOUR - 5 * HOUR, recorded: true, durationS: 58 }],
    [buildExpense, { verdictId: 'v_rec_er', editId: 'e_rec_er', at: now - 2 * 24 * HOUR - 4 * HOUR, recorded: true, durationS: 63 }],
    [buildReleaseWarn, { verdictId: 'v_7d20e8', editId: 'e_7d20e8', at: now - 26 * HOUR, durationS: 52 }],
    [buildTriage, { verdictId: 'v_rec_gt', editId: 'e_rec_gt', at: now - 3 * HOUR - 12 * 60_000, recorded: true, durationS: 47 }],
  ]
  const blessedSeen = new Set<string>()
  for (const [fn, opts, mutate] of history) {
    const b = fn(opts)
    mutate?.(b)
    for (const g of b.goldens) {
      if (blessedSeen.has(g.run_id) || !g.blessed_at) continue
      blessedSeen.add(g.run_id)
    }
    add(b)
  }
  // Events in chronological order: blessings first, then each verdict's lifecycle.
  const goldenFirst = new Map<string, Trace>()
  for (const b of builts.values()) for (const g of b.goldens) if (!goldenFirst.has(g.run_id)) goldenFirst.set(g.run_id, g)
  // Recent runs nobody has blessed yet (GET /runs?unblessed=1): fresh runs of a few tasks.
  const runSource = new Map<string, RunView['source']>()
  const liveTraces: Trace[] = []
  for (const g of goldenFirst.values()) {
    if (liveTraces.length === 3) break
    if (liveTraces.some((t) => t.skills_invoked[0] === g.skills_invoked[0])) continue
    const i = liveTraces.length
    const run_id = `run_live_0${i + 1}`
    runSource.set(run_id, i < 2 ? 'captured' : 'live')
    liveTraces.push({ ...structuredClone(g), run_id, kind: 'live', blessed_at: null, golden_of: null, archived_reason: null, created_at: new Date(now - (i + 1) * 47 * 60_000).toISOString() })
  }
  const timeline: { ts: number; fn: () => void }[] = []
  // Only a handful of blessings, so the timeline reads like a week of use, not a data dump.
  const blessPick = ['run_gt_01', 'run_er_01', 'run_fx_01', 'run_fx_02', 'run_rel_01', 'run_cha_01']
  for (const id of blessPick) {
    const g = goldenFirst.get(id)
    if (!g?.blessed_at) continue
    const ts = new Date(g.blessed_at).getTime()
    if (ts > now) continue
    timeline.push({ ts, fn: () => emit('trace.blessed', g.run_id, { run_id: g.run_id, task: g.task_label, skills_invoked: g.skills_invoked, world_dependent: g.world_dependent, n_facts: g.world_facts.length }, ts) })
  }
  for (const b of builts.values()) {
    const v = b.verdict
    const at = new Date(b.edit.detected_at).getTime()
    const done = new Date(v.created_at).getTime()
    timeline.push({ ts: at, fn: () => emit('edit.detected', b.edit.edit_id, { edit_id: b.edit.edit_id, verdict_id: v.verdict_id, commit: b.edit.commit, affected_skills: b.edit.affected_skills, author: b.edit.author, summary: b.edit.summary }, at) })
    v.pairs
      .filter((p) => p.control_run_id)
      .forEach((p, i) => {
        const ts = done - 9000 + i * 400
        timeline.push({ ts, fn: () => emit('pair.control', b.edit.edit_id, controlPayload(b.edit.edit_id, v.verdict_id, p), ts) })
      })
    timeline.push({ ts: done, fn: () => emit('verdict.decided', v.verdict_id, decidedPayload(v), done) })
    timeline.push({ ts: done + 1500, fn: () => emit('verdict.acted', v.verdict_id, { verdict_id: v.verdict_id, action: v.rolled_back_at ? 'merged' : v.action, commit: v.rolled_back_at ? hex(`${v.verdict_id}:merge`, 40) : v.action_commit, skill: v.affected_skills[0], decision: v.decision }, done + 1500) })
    if (v.rolled_back_at) {
      const rb = new Date(v.rolled_back_at).getTime()
      timeline.push({ ts: rb, fn: () => emit('verdict.rolled_back', v.verdict_id, { verdict_id: v.verdict_id, actor: v.rolled_back_by, skill: v.affected_skills[0], commit: v.action_commit }, rb) })
    }
  }
  const digestTs = now - 20 * HOUR
  timeline.push({ ts: digestTs, fn: () => emit('digest.written', 'dg_w42', { period: 'week 42', verdict_ids: [...builts.keys()] }, digestTs) })
  timeline.sort((a, b) => a.ts - b.ts).forEach((t) => t.fn())

  /** `pair.control` (ADR 0023). The backend sends no task label; the fixture adds one so the timeline reads well. */
  function controlPayload(editId: string, verdictId: string, p: Verdict['pairs'][number]) {
    return { edit_id: editId, verdict_id: verdictId, golden: p.golden_run_id, replay: p.replay_run_id, control: p.control_run_id, control_outcome: p.control_outcome ?? null, task_label: p.task_label }
  }

  function decidedPayload(v: Verdict) {
    const n: Record<string, number> = {}
    for (const p of v.pairs) n[p.outcome] = (n[p.outcome] ?? 0) + 1
    return { verdict_id: v.verdict_id, decision: v.decision, n_by_outcome: n, usd_by_tier: v.cost, duration_s: v.duration_s, headline: v.headline, affected_skills: v.affected_skills }
  }

  // ---------------------------------------------------------------- simulate engine
  function planFor(id: string): [StageKey, number][] {
    const p = PLANS[id] ?? {}
    const s = speed()
    return STAGE_KEYS.filter((k) => k !== 'done').map((k) => [k, (p[k] ?? 0) / s])
  }

  function stageCounts(run: Run): Record<string, number | null> {
    const pairs = run.built.verdict.pairs
    const toNano = pairs.filter((p) => p.opinions.some((o) => o.tier === 'nano')).length
    const toSuper = pairs.filter((p) => p.opinions.some((o) => o.tier === 'super')).length
    const toUltra = pairs.filter((p) => p.opinions.some((o) => o.tier === 'ultra')).length
    const facts = new Set(pairs.flatMap((p) => p.drift.map((d) => d.fact_id))).size
    const controls = pairs.filter((p) => p.control_run_id).length
    return { selecting: pairs.length, replaying: pairs.length, nano: toNano, super: toSuper, ultra: toUltra, controls, drift: facts }
  }

  function tierCostInStage(run: Run, tier: Tier): number {
    return run.built.verdict.cost[tier] ?? 0
  }

  function progressOf(run: Run, at = Date.now()): { progress: Progress; current: StageKey; k: number; ck: number; done: boolean } {
    const plan = planFor(run.scenarioId)
    const counts = stageCounts(run)
    const t = run.startAt == null ? 0 : (at - run.startAt) / 1000
    let acc = 0
    let current: StageKey = 'done'
    let frac = 1
    for (const [k, d] of plan) {
      if (d <= 0) continue
      if (t < acc + d) {
        current = k
        frac = (t - acc) / d
        break
      }
      acc += d
    }
    const idx = STAGE_KEYS.indexOf(current)
    const n = counts.replaying ?? 0
    const k = current === 'replaying' ? Math.min(n, Math.floor(frac * (n + 1))) : idx > STAGE_KEYS.indexOf('replaying') ? n : 0
    // Control replays (ADR 0023) count their own `ctrl_…` batches, never the edit's replays.
    const cn = counts.controls ?? 0
    const ck = current === 'controls' ? Math.min(cn, Math.floor(frac * (cn + 1))) : idx > STAGE_KEYS.indexOf('controls') ? cn : 0
    // Running cost: each tier's spend accrues during its stage; narrative adds Ultra spend.
    const v = run.built.verdict
    const narrativeUltra = price('ultra', 5200, 360)
    const judgeUltra = Math.max(0, tierCostInStage(run, 'ultra') - narrativeUltra)
    const share = (stage: StageKey) => {
      const si = STAGE_KEYS.indexOf(stage)
      if (idx > si) return 1
      if (idx === si) return Math.max(0, Math.min(1, frac))
      return 0
    }
    const usdByTier: TierMap<number> = {
      nano: (v.cost.nano ?? 0) * share('nano'),
      super: (v.cost.super ?? 0) * share('super'),
      ultra: judgeUltra * share('ultra') + narrativeUltra * share('summary'),
    }
    const stagesOut: ProgressStage[] = STAGE_KEYS.map((key) => {
      const d = key === 'done' ? 1 : (plan.find(([pk]) => pk === key)?.[1] ?? 0)
      const ki = STAGE_KEYS.indexOf(key)
      const skipped = key !== 'done' && d <= 0
      const status: ProgressStage['status'] = skipped ? 'skipped' : ki < idx ? 'done' : ki === idx ? (key === 'done' ? 'done' : 'active') : 'pending'
      const cnt = counts[key] ?? null
      return {
        key,
        label: skipped
          ? stageLabel(key)
          : key === 'controls'
            ? stageLabel(key, ck, cn)
            : stageLabel(key, key === 'replaying' ? (ki === idx ? k : ki < idx ? n : 0) : null, cnt),
        status,
        detail: skipped ? 'Not needed' : stageDetail(key, run, status),
      }
    })
    const elapsed = run.startAt == null ? 0 : Math.max(0, (at - run.startAt) / 1000)
    // Like ratchet/orchestrator/progress.py, Progress.k/n stay the replay counts during controls.
    const controlsNow = current === 'controls'
    return {
      current,
      k,
      ck,
      done: current === 'done',
      progress: {
        stage: current,
        stage_label: controlsNow ? stageLabel(current, ck, cn) : stageLabel(current, current === 'replaying' ? k : null, counts[current] ?? null),
        k: current === 'replaying' ? k : controlsNow ? n : null,
        n: current === 'replaying' || controlsNow ? n : (counts[current] ?? null),
        elapsed_s: Math.round(elapsed * 10) / 10,
        usd_by_tier: usdByTier,
        stalled: false,
        stages: stagesOut,
      },
    }
  }

  function stageDetail(key: StageKey, run: Run, status: ProgressStage['status']): string | null {
    if (status === 'pending') return null
    const v = run.built.verdict
    const pairs = v.pairs
    switch (key) {
      case 'detected':
        return run.built.edit.affected_skills.join(', ')
      case 'selecting':
        return `${Math.ceil(pairs.length * 0.6)} direct, ${pairs.length - Math.ceil(pairs.length * 0.6)} neighbors`
      case 'replaying':
        return 'super, as originally run'
      case 'level0': {
        const hard = pairs.filter((p) => p.level0.hard_fail).length
        return status === 'done' ? (hard ? `${hard} hard fails` : 'No new risky calls') : null
      }
      case 'nano':
      case 'super':
      case 'ultra':
        return status === 'done' ? `$${(v.cost[key] ?? 0).toFixed(4)} (${key})` : null
      case 'controls': {
        const c = pairs.filter((p) => p.control_run_id).length
        return status === 'done' ? `${c} pair(s) re-checked` : null
      }
      case 'drift':
        return status === 'done' ? 'EUR/USD 1.0800 → 1.1630' : null
      case 'summary':
        return status === 'done' ? 'Nemotron Ultra' : null
      default:
        return null
    }
  }

  function viewOf(id: string): VerdictView {
    const run = runs.get(id)
    const b = builts.get(id)
    if (run && !run.finished) {
      if (run.startAt == null) {
        return { verdict_id: id, state: 'queued', verdict: null, edit: run.built.edit, diff: run.built.diff, progress: null }
      }
      const { progress, current } = progressOf(run)
      return { verdict_id: id, state: stateFor(current), verdict: null, edit: run.built.edit, diff: run.built.diff, progress }
    }
    if (!b) throw new ApiError('not_found', 'Verdict not found', 404, 'req_' + hex(id, 8))
    const state = (actingUntil.get(id) ?? 0) > Date.now() ? 'acting' : 'done'
    return { verdict_id: id, state, verdict: b.verdict, edit: b.edit, diff: b.diff, progress: null }
  }

  function stateFor(stage: StageKey): string {
    const m: Record<StageKey, string> = { detected: 'detected', selecting: 'selecting', replaying: 'collecting', level0: 'judging', nano: 'judging', super: 'judging', ultra: 'judging', controls: 'controlling', drift: 'drifting', summary: 'deciding', done: 'done' }
    return m[stage]
  }

  function tick() {
    const t = Date.now()
    for (const run of runs.values()) {
      if (run.finished) continue
      if (run.startAt == null) {
        const busy = [...runs.values()].some((r) => r !== run && !r.finished && r.startAt != null)
        if (!busy) {
          run.startAt = t
          emit('simulate.started', run.verdictId, { scenario: run.scenarioId, session_hash: SESSION, verdict_id: run.verdictId, status: 'running' }, t, SESSION)
        } else continue
      }
      const { current, k, ck, progress } = progressOf(run, t)
      if (current !== run.lastStage) {
        if (run.lastStage) emit('orchestrator.state', run.editId, { edit_id: run.editId, verdict_id: run.verdictId, from: run.lastStage, to: current, elapsed_ms: Math.round(progress.elapsed_s * 1000) }, t, SESSION)
        if (current === 'detected') emit('edit.detected', run.editId, { edit_id: run.editId, verdict_id: run.verdictId, commit: run.built.edit.commit, affected_skills: run.built.edit.affected_skills, author: 'seeded', summary: run.built.edit.summary }, t, SESSION)
        if (current === 'selecting') emit('edit.selected', run.editId, { edit_id: run.editId, verdict_id: run.verdictId, n_traces: run.built.verdict.pairs.length }, t, SESSION)
        run.lastStage = current
      }
      if (current === 'replaying' && k !== run.lastK) {
        run.lastK = k
        const batch = run.built.verdict.batch_ids[0] ?? 'batch'
        emit('batch.heartbeat', batch, { edit_id: run.editId, verdict_id: run.verdictId, batch_id: batch, runner: 'nebius-serverless', k, n: run.built.verdict.pairs.length, status: 'running' }, t, SESSION)
      }
      advanceControls(run, current, ck, t)
      if (current === 'done') finish(run, t)
    }
  }

  /**
   * ADR 0023 control replays: one `ctrl_…` batch (submitted → heartbeat → finished), a
   * `pair.control` per resolved pair, and `pair.decided` again when the outcome changed.
   * Catches up in one tick when a fast fixture speed skips the stage between ticks.
   */
  const controlSubmitted = new Set<string>()
  function advanceControls(run: Run, current: StageKey, ck: number, t: number) {
    const controlled = run.built.verdict.pairs.filter((p) => p.control_run_id)
    if (!controlled.length) return
    const ci = STAGE_KEYS.indexOf('controls')
    const here = STAGE_KEYS.indexOf(current)
    if (here < ci) return
    const batch = controlBatchId(run.verdictId)
    const base = { edit_id: run.editId, verdict_id: run.verdictId, batch_id: batch, runner: 'nebius-serverless', n: controlled.length }
    const target = here > ci ? controlled.length : ck
    if (!controlSubmitted.has(batch)) {
      controlSubmitted.add(batch)
      emit('batch.submitted', batch, { ...base, k: 0, status: 'submitted' }, t, SESSION)
    }
    if (target <= run.lastCK) return
    for (let i = run.lastCK; i < target; i++) {
      const p = controlled[i]!
      emit('batch.heartbeat', batch, { ...base, k: i + 1, status: 'running' }, t, SESSION)
      emit('pair.control', run.editId, controlPayload(run.editId, run.verdictId, p), t, SESSION)
      if (p.decided_by === 'control') emit('pair.decided', run.editId, { edit_id: run.editId, verdict_id: run.verdictId, golden: p.golden_run_id, replay: p.replay_run_id, outcome: p.outcome }, t, SESSION)
    }
    run.lastCK = target
    if (target === controlled.length) emit('batch.finished', batch, { ...base, k: target, status: 'succeeded' }, t, SESSION)
  }

  function finish(run: Run, t: number) {
    run.finished = true
    const v = run.built.verdict
    v.created_at = new Date(t).toISOString()
    v.duration_s = Math.round((t - (run.startAt ?? t)) / 1000)
    add(run.built)
    // The orchestrator still acts (revert / hold / merge) and notifies after deciding.
    actingUntil.set(v.verdict_id, t + 1500 / speed())
    emit('verdict.decided', v.verdict_id, decidedPayload(v), t, SESSION)
    emit('verdict.acted', v.verdict_id, { verdict_id: v.verdict_id, action: v.action, commit: v.action_commit, skill: v.affected_skills[0], decision: v.decision }, t + 1, SESSION)
    emit('simulate.finished', v.verdict_id, { scenario: run.scenarioId, session_hash: SESSION, verdict_id: v.verdict_id, status: 'done' }, t + 2, SESSION)
  }

  let timer: ReturnType<typeof setInterval> | null = null
  function ensureTimer() {
    if (timer) return
    timer = setInterval(() => {
      tick()
      if ([...runs.values()].every((r) => r.finished) && timer) {
        clearInterval(timer)
        timer = null
      }
    }, 200)
  }

  let simCount = 0

  // ---------------------------------------------------------------- derived views
  /** Unbless/Bless state per golden run id (the real store flips `kind` and `blessed_at`). */
  const blessState = new Map<string, Pick<Trace, 'kind' | 'blessed_at'>>()
  const withBless = (t: Trace): Trace => {
    const o = blessState.get(t.run_id)
    return o ? { ...t, ...o } : t
  }

  function findRawTrace(runId: string): Trace | undefined {
    const live = liveTraces.find((t) => t.run_id === runId)
    if (live) return live
    for (const b of builts.values()) {
      const t = b.goldens.find((g) => g.run_id === runId) ?? b.replays.find((r) => r.run_id === runId)
      if (t) return t
    }
    for (const r of runs.values()) {
      const t = r.built.goldens.find((g) => g.run_id === runId)
      if (t) return t
    }
    return undefined
  }
  function findTrace(runId: string): Trace | undefined {
    const t = findRawTrace(runId)
    return t ? withBless(t) : undefined
  }

  function costTile(verdicts: Verdict[]) {
    const usd: TierMap<number> = { nano: 0, super: 0, ultra: 0 }
    const tok: TierMap<number> = { nano: 0, super: 0, ultra: 0 }
    for (const v of verdicts)
      for (const t of ['nano', 'super', 'ultra'] as Tier[]) {
        usd[t] = (usd[t] ?? 0) + (v.cost[t] ?? 0)
        tok[t] = (tok[t] ?? 0) + (v.tokens[t] ?? 0)
      }
    // Prompt-cache share: judge prompt tokens by tier, a fixed share of them served from cache.
    const prompt: TierMap<number> = { nano: 0, super: 0, ultra: 0 }
    const cached: TierMap<number> = { nano: 0, super: 0, ultra: 0 }
    for (const v of verdicts)
      for (const o of v.pairs.flatMap((p) => p.opinions)) {
        prompt[o.tier] = (prompt[o.tier] ?? 0) + o.prompt_tokens
        cached[o.tier] = (cached[o.tier] ?? 0) + Math.round(o.prompt_tokens * FIXTURE_CACHE_RATE[o.tier])
      }
    const promptTotal = Object.values(prompt).reduce((a, b) => a + (b ?? 0), 0)
    const cachedTotal = Object.values(cached).reduce((a, b) => a + (b ?? 0), 0)
    return {
      usd_by_tier: usd,
      tokens_by_tier: tok,
      prompt_tokens_by_tier: prompt,
      cached_prompt_tokens_by_tier: cached,
      cache_share: promptTotal > 0 ? cachedTotal / promptTotal : null,
    }
  }

  function liveTicker() {
    const startOfDay = new Date()
    startOfDay.setHours(0, 0, 0, 0)
    const today = [...builts.values()].map((b) => b.verdict).filter((v) => new Date(v.created_at) >= startOfDay)
    const tile = costTile(today)
    let live = false
    for (const run of runs.values()) {
      if (run.finished || run.startAt == null) continue
      live = true
      const { progress } = progressOf(run)
      for (const t of ['nano', 'super', 'ultra'] as Tier[]) tile.usd_by_tier[t] = (tile.usd_by_tier[t] ?? 0) + (progress.usd_by_tier[t] ?? 0)
    }
    const total = Object.values(tile.usd_by_tier).reduce((a, b) => a + (b ?? 0), 0)
    return { usd_by_tier: tile.usd_by_tier, total_usd: total, live }
  }

  const meta: Meta = {
    public_mode: true,
    simulate_enabled: true,
    simulate_mode: 'live',
    simulated_models: false,
    action_token: 'fixture-token',
    repo_url: 'https://github.com/lloydjf/ratchet',
    video_url: 'https://www.youtube.com/watch?v=ratchet-demo',
    bench_report_url: 'https://github.com/lloydjf/ratchet/blob/main/bench/results/report.md',
    version: '0.1.0',
    harness: 'hermes',
    runner: 'nebius-serverless',
    sandbox: 'openshell',
  }
  try {
    const q = new URLSearchParams(window.location.search)
    if (q.get('operator') === '1') sessionStorage.setItem('ratchet.fixture.operator', '1')
    if (q.get('operator') === '0') sessionStorage.removeItem('ratchet.fixture.operator')
    if (sessionStorage.getItem('ratchet.fixture.operator') === '1') meta.public_mode = false
    if (q.get('simulated') === '1') meta.simulated_models = true
  } catch {
    /* storage unavailable */
  }

  function mutateVerdict(id: string, fn: (v: Verdict) => void): VerdictView {
    const b = builts.get(id)
    const run = runs.get(id)
    // ratchet/orchestrator/actions.py `_ready_edit`: actions wait for a terminal edit state.
    if ((run && !run.finished) || (actingUntil.get(id) ?? 0) > Date.now())
      throw new ApiError('not_ready', 'this edit is still being acted on', 409)
    if (!b) throw new ApiError('not_found', 'Verdict not found', 404)
    fn(b.verdict)
    return viewOf(id)
  }

  const api: RatchetApi = {
    mode: 'fixtures',
    getMeta: () => delay(meta, 40),
    listScenarios: () => delay(SCENARIOS),
    async simulate(scenarioId) {
      const builder = BUILDERS[scenarioId]
      if (!builder) throw new ApiError('not_found', 'Unknown scenario', 404)
      simCount += 1
      const verdictId = `v_sim${simCount}_${hex(scenarioId + Date.now(), 6)}`
      const editId = `e_sim${simCount}_${hex(scenarioId + Date.now(), 6)}`
      const built = builder({ verdictId, editId, at: Date.now(), session: SESSION, durationS: 20 })
      const busy = [...runs.values()].filter((r) => !r.finished)
      const run: Run = { scenarioId, verdictId, editId, startAt: null, queuedAt: Date.now(), built, lastStage: null, lastK: -1, lastCK: 0, finished: false }
      runs.set(verdictId, run)
      let status: SimulateResponse['status'] = 'running'
      let eta: number | null = null
      if (busy.length) {
        status = 'queued'
        eta = busy.reduce((a, r) => a + Math.max(0, 20 / speed() - (r.startAt ? (Date.now() - r.startAt) / 1000 : 0)), 0)
      } else tick()
      ensureTimer()
      return delay<SimulateResponse>({ session_hash: SESSION, scenario_id: scenarioId, edit_id: editId, verdict_id: verdictId, status, queue_position: busy.length || null, eta_s: eta }, 160)
    },
    async listVerdicts() {
      const items = order.map((id) => summarize(builts.get(id)!.verdict))
      return delay<Page<VerdictSummary>>({ items, next_cursor: null })
    },
    getVerdict: async (id) => delay(viewOf(id), 90),
    async getPair(id, g, r) {
      const b = builts.get(id)
      if (!b) throw new ApiError('not_found', 'Verdict not found', 404)
      const pair = b.verdict.pairs.find((p) => p.golden_run_id === g && p.replay_run_id === r)
      const golden = b.goldens.find((x) => x.run_id === g)
      const replay = b.replays.find((x) => x.run_id === r)
      if (!pair || !golden || !replay) throw new ApiError('not_found', 'Pair not found', 404)
      return delay({ verdict_id: id, decision: b.verdict.decision, recorded: b.verdict.recorded, pair, golden: reblessed.has(`${g}/${r}`) ? { ...golden, archived_reason: 'world changed' } : golden, replay })
    },
    // Mirrors ratchet/orchestrator/actions.py. Rollback refuses a reverted verdict (a BLOCK,
    // or one already rolled back: 409 already_reverted); on a merged edit it takes the edit
    // out of main. Approve on a reverted verdict is the override ("approve anyway"): the
    // revert is re-applied and merged. Both answer 409 not_ready until the edit is terminal.
    async rollback(id) {
      let merged = false
      const view = mutateVerdict(id, (v) => {
        if (v.action === 'reverted') throw new ApiError('already_reverted', 'this edit is already rolled back', 409)
        merged = v.action === 'merged'
        v.action = 'reverted'
        v.rolled_back_by = 'you'
        v.rolled_back_at = new Date().toISOString()
        v.action_commit = v.recorded ? null : hex(`${id}:rollback`, 40)
      })
      const toast = merged ? successToast.rollbackMerged() : successToast.rollback()
      emit('verdict.rolled_back', id, { verdict_id: id, edit_id: view.edit.edit_id, actor: 'you', commit: view.verdict?.action_commit ?? null, toast, skill: view.verdict?.affected_skills[0] }, Date.now(), SESSION)
      return delay<VerdictView>({ ...view, toast }, 420)
    },
    async approve(id) {
      const wasMerged = builts.get(id)?.verdict.action === 'merged'
      let override = false
      const view = mutateVerdict(id, (v) => {
        if (v.action === 'merged') return
        override = v.action === 'reverted'
        v.action = 'merged'
        v.approved_at = new Date().toISOString()
        v.action_commit = v.recorded ? null : hex(`${id}:approve`, 40)
      })
      if (wasMerged) return delay(view, 200)
      const skill = view.edit.affected_skills.join(', ') || 'the skill'
      const toast = override ? successToast.approveAnyway(skill) : successToast.approve(skill)
      emit('verdict.acted', id, { verdict_id: id, edit_id: view.edit.edit_id, action: 'merged', commit: view.verdict?.action_commit ?? null, actor: 'you', override, toast, skill: view.verdict?.affected_skills[0], decision: view.verdict?.decision }, Date.now(), SESSION)
      return delay<VerdictView>({ ...view, toast }, 420)
    },
    async rebless(g, r) {
      reblessed.add(`${g}/${r}`)
      const res = { new_golden_run_id: `run_${hex(r, 8)}`, archived_run_id: g, toast: successToast.rebless() }
      const pair = [...builts.values()].flatMap((b) => b.verdict.pairs).find((p) => p.golden_run_id === g && p.replay_run_id === r)
      const driftFactIds = pair?.drift.filter((d) => d.changed).map((d) => d.fact_id) ?? []
      emit('pair.reblessed', g, { golden: g, replay: r, new_golden: res.new_golden_run_id, archived: g, session_scoped: true, drift_fact_ids: driftFactIds, actor: 'you' }, Date.now(), SESSION)
      return delay(res, 380)
    },
    async listEdits() {
      const items: EditView[] = order.map((id) => {
        const b = builts.get(id)!
        return { edit: b.edit, diff: b.diff, verdict_id: id, state: 'done' }
      })
      return delay({ items, next_cursor: null })
    },
    async getEdit(id) {
      for (const [vid, b] of builts) if (b.edit.edit_id === id) return delay<EditView>({ edit: b.edit, diff: b.diff, verdict_id: vid, state: 'done' })
      for (const r of runs.values()) if (r.editId === id) return delay<EditView>({ edit: r.built.edit, diff: r.built.diff, verdict_id: r.verdictId, state: viewOf(r.verdictId).state })
      throw new ApiError('not_found', 'Edit not found', 404)
    },
    async replayEdit(id) {
      return api.getEdit(id)
    },
    async listTraces(q = {}) {
      const seen = new Map<string, TraceSummary>()
      const kind = q.kind ?? 'golden'
      for (const b of builts.values())
        for (const raw of b.goldens) {
          const g = withBless(raw)
          if (g.kind !== kind) continue
          if (seen.has(g.run_id)) {
            const s = seen.get(g.run_id)!
            if (b.verdict.created_at > (s.last_replayed_at ?? '')) s.last_replayed_at = b.verdict.created_at
            continue
          }
          seen.set(g.run_id, { run_id: g.run_id, kind: g.kind, task_input: g.task_input, task_label: g.task_label, skills_invoked: g.skills_invoked, world_dependent: g.world_dependent, n_facts: g.world_facts.length, last_replayed_at: b.verdict.created_at, blessed_at: g.blessed_at, created_at: g.created_at })
        }
      let items = [...seen.values()]
      if (q.skill) items = items.filter((t) => t.skills_invoked.includes(q.skill!))
      if (q.search) {
        const s = q.search.toLowerCase()
        items = items.filter((t) => t.task_input.toLowerCase().includes(s) || t.task_label.toLowerCase().includes(s))
      }
      items.sort((a, b) => (b.blessed_at ?? '').localeCompare(a.blessed_at ?? ''))
      return delay({ items, next_cursor: null })
    },
    async listRuns(q = {}) {
      if (meta.public_mode) throw new ApiError('not_public', 'Not available on the demo.', 403)
      const seen = new Set<string>()
      const rows: RunView[] = []
      for (const raw of [...liveTraces, ...goldenFirst.values()]) {
        if (seen.has(raw.run_id)) continue
        seen.add(raw.run_id)
        const t = withBless(raw)
        const blessed = t.kind === 'golden'
        if ((q.unblessed !== false && blessed) || t.archived_reason) continue
        const last = t.steps.reduce((a, s) => (s.started_at && s.started_at > a ? s.started_at : a), t.created_at)
        rows.push({ run_id: t.run_id, source: blessed ? 'golden' : (runSource.get(t.run_id) ?? 'live'), task_preview: t.task_input.slice(0, 160), task_label: t.task_label, agent_tier: t.agent_tier, n_steps: t.steps.length, last_activity_at: last, blessed })
      }
      rows.sort((a, b) => b.last_activity_at.localeCompare(a.last_activity_at))
      return delay({ items: rows.slice(0, q.limit ?? 50) })
    },
    async getTrace(runId) {
      const t = findTrace(runId)
      if (!t) throw new ApiError('not_found', 'Trace not found', 404)
      return delay(t)
    },
    async bless(runId) {
      if (meta.public_mode) throw new ApiError('not_public', 'Not available on the demo.', 403)
      const t = findTrace(runId)
      if (!t) throw new ApiError('not_found', 'Trace not found', 404)
      if (t.kind !== 'live') throw new ApiError('conflict', 'Only live runs can be blessed.', 409)
      const blessed_at = new Date().toISOString()
      blessState.set(runId, { kind: 'golden', blessed_at })
      emit('trace.blessed', runId, { run_id: runId, task: t.task_label, skills_invoked: t.skills_invoked, world_dependent: t.world_dependent, n_facts: t.world_facts.length }, Date.now(), SESSION)
      return delay({ ...t, kind: 'golden', blessed_at }, 600)
    },
    async unbless(runId) {
      if (meta.public_mode) throw new ApiError('not_public', 'Not available on the demo.', 403)
      const t = findTrace(runId)
      if (!t) throw new ApiError('not_found', 'Trace not found', 404)
      if (t.kind !== 'golden') throw new ApiError('conflict', 'Only goldens can be unblessed.', 409)
      blessState.set(runId, { kind: 'live', blessed_at: null })
      emit('trace.unblessed', runId, { run_id: runId }, Date.now(), SESSION)
      return delay({ ...t, kind: 'live', blessed_at: null })
    },
    async putCriteria(runId, criteria) {
      const t = findRawTrace(runId)
      if (!t) throw new ApiError('not_found', 'Trace not found', 404)
      t.success_criteria = criteria
      return delay(withBless(t))
    },
    async getCosts(range) {
      const all = [...builts.values()].map((b) => b.verdict)
      const startOfDay = new Date()
      startOfDay.setHours(0, 0, 0, 0)
      const at = (v: Verdict) => new Date(v.created_at).getTime()
      const week = all.filter((v) => at(v) > Date.now() - 7 * 24 * HOUR)
      const today = all.filter((v) => new Date(v.created_at) >= startOfDay)
      const last = [...all].sort((a, b) => b.created_at.localeCompare(a.created_at))[0]
      // Mirrors routes/costs.py `_window`: [from, to), from < to, at most 366 days.
      let win: { from: number; to: number } | null = null
      if (range) {
        const from = Date.parse(range.from)
        const to = Date.parse(range.to)
        if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to || to - from > 366 * 24 * HOUR)
          throw new ApiError('bad_request', 'The from/to range is not valid (from < to, at most 366 days).', 400)
        win = { from, to }
      }
      const inWin = win ? all.filter((v) => at(v) >= win.from && at(v) < win.to) : week
      const agentReplays = inWin.reduce((a, v) => a + v.pairs.length, 0)
      const byPurpose: CostsView['by_purpose'] = []
      const opinions = inWin.flatMap((v) => v.pairs.flatMap((p) => p.opinions))
      const sumOp = (tier: Tier) => {
        const os = opinions.filter((o) => o.tier === tier)
        return { calls: os.length, usd: os.reduce((a, o) => a + o.usd, 0), tokens: os.reduce((a, o) => a + o.prompt_tokens + o.completion_tokens, 0) }
      }
      if (inWin.length) {
        byPurpose.push({ purpose: 'agent', tier: 'super', calls: agentReplays * 3, usd: agentReplays * price('super', 12_600, 1_100), tokens: agentReplays * 13_700 })
        byPurpose.push({ purpose: 'screen', tier: 'nano', ...sumOp('nano') })
        byPurpose.push({ purpose: 'check', tier: 'super', ...sumOp('super') })
        byPurpose.push({ purpose: 'adjudicate', tier: 'ultra', ...sumOp('ultra') })
        byPurpose.push({ purpose: 'narrative', tier: 'ultra', calls: inWin.length, usd: inWin.length * price('ultra', 5200, 360), tokens: inWin.length * 5560 })
        const extract = inWin.length * 6
        byPurpose.push({ purpose: 'extract', tier: 'nano', calls: extract, usd: extract * price('nano', 3100, 240), tokens: extract * 3340 })
      }
      const dayKey = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      const days: CostsView['by_day'] = []
      const firstDay = win ? new Date(win.from) : new Date(startOfDay.getTime() - 13 * 24 * HOUR)
      firstDay.setHours(0, 0, 0, 0)
      const endAt = win ? win.to : startOfDay.getTime() + 24 * HOUR
      for (let start = firstDay; start.getTime() < endAt; start = new Date(start.getTime() + 24 * HOUR)) {
        const lo = Math.max(start.getTime(), win?.from ?? 0)
        const hi = Math.min(start.getTime() + 24 * HOUR, endAt)
        const vs = all.filter((v) => at(v) >= lo && at(v) < hi)
        days.push({ day: dayKey(start), usd_by_tier: costTile(vs).usd_by_tier })
      }
      // Mirrors routes/costs.py: the counterfactual covers the same verdicts as the window.
      const actualByTier = costTile(inWin).usd_by_tier
      const actual = Object.values(actualByTier).reduce((a, b) => a + (b ?? 0), 0)
      const cf = inWin.reduce((a, v) => a + v.counterfactual_usd, 0)
      const out: CostsView = {
        today: costTile(today),
        week: costTile(week),
        last_edit: last ? { ...costTile([last]), edit_id: last.edit_id, verdict_id: last.verdict_id, counterfactual_usd: last.counterfactual_usd } : null,
        by_purpose: byPurpose,
        by_day: days,
        counterfactual: { actual_usd: actual, actual_usd_by_tier: actualByTier, all_ultra_usd: cf, saved_ratio: actual ? cf / actual : 0, n_verdicts: inWin.length },
        bench_report_url: meta.bench_report_url,
        window: win ? { ...costTile(inWin), from: new Date(win.from).toISOString(), to: new Date(win.to).toISOString() } : null,
      }
      return delay(out)
    },
    async getEditLogs(id) {
      if (meta.public_mode) throw new ApiError('not_public', 'Not available on the demo.', 403)
      const entry = [...builts.entries()].find(([, b]) => b.edit.edit_id === id)
      if (!entry) throw new ApiError('not_found', 'Edit not found', 404)
      const [vid, b] = entry
      const v = b.verdict
      const lines = [
        ...events.filter((e) => e.payload.verdict_id === vid || e.ref === id).map((e) => `${e.ts} ${e.kind} ${e.ref}`),
        ...v.batch_ids.flatMap((batch) => [
          `--- worker log ${batch}`,
          ...v.pairs.map((p) => `${p.replay_run_id} replay ${p.outcome === 'failed' ? `failed ${p.error_code ?? ''}`.trim() : 'ok'}`),
        ]),
      ]
      // The server keeps the last 500 lines and says so (LogsView.truncated).
      return delay({ lines: lines.slice(-500), truncated: lines.length > 500 })
    },
    async getDigest() {
      const d: Digest = {
        digest_id: 'dg_w42',
        period_start: new Date(now - 7 * 24 * HOUR).toISOString(),
        period_end: new Date(now - 20 * HOUR).toISOString(),
        narrative:
          'This week your agent changed five skills and Ratchet checked each change against the tasks you blessed. Two edits were blocked. The github-triage edit tried to close issues the original runs only labeled, and the expense-report edit dropped the VAT line from three past reports; both were reverted before they reached main.\n\nOne edit is waiting for you. The release-notes change renames the Fixes section, and the judges could not agree whether that still separates fixes from features. The fx-expense-converter edit passed even though four totals changed, because the EUR/USD rate moved, not the skill. The changelog-writer edit merged, and Jun rolled it back two hours later.\n\nJudging all five edits cost $0.28 across Nano, Super and Ultra, about a third of what an all-Ultra judge would have spent.',
        verdict_ids: [...builts.keys()],
        merged: [
          { edit_id: 'e_rec_fx', verdict_id: 'v_rec_fx', skill: 'fx-expense-converter', summary: 'Rewords the rounding note; four tasks re-blessed after the EUR/USD move.' },
          { edit_id: 'e_9a41c2', verdict_id: 'v_9a41c2', skill: 'changelog-writer', summary: 'Tightens the entry length rule to one line. Later rolled back by Jun.' },
        ],
        blocked: [
          { edit_id: 'e_rec_gt', verdict_id: 'v_rec_gt', skill: 'github-triage', summary: 'Would close issues in 6 of 14 replays.' },
          { edit_id: 'e_rec_er', verdict_id: 'v_rec_er', skill: 'expense-report', summary: 'Drops the VAT line on 3 of 10 past tasks.' },
        ],
        // Product-metrics rows, as `ratchet metrics` prints them (ratchet/orchestrator/metrics.py).
        metrics: [
          { metric: 'Blessed traces', value: '42' },
          { metric: 'Protected edits', value: '5' },
          { metric: 'Decision mix', value: 'PASS 40%, WARN 20%, BLOCK 40%' },
          { metric: 'Block precision proxy', value: '100%' },
          { metric: 'False-alarm proxy', value: '33%' },
          { metric: 'Edit to verdict p50', value: '52 s' },
          { metric: 'Edit to verdict p95', value: '63 s' },
        ],
      }
      return delay(d)
    },
    async listEvents(q = {}) {
      let items = events.slice()
      if (q.since != null) items = items.filter((e) => e.id > q.since!)
      if (q.kinds?.length) items = items.filter((e) => q.kinds!.includes(e.kind))
      items.reverse()
      return delay({ items: items.slice(0, q.limit ?? 200), next_cursor: null }, 80)
    },
    getTicker: async () => delay(liveTicker(), 30),
    async getSettings() {
      if (meta.public_mode) throw new ApiError('not_public', 'Not available on the demo.', 403)
      const s: SettingsView = {
        caps: [
          { purpose: 'agent', usd_per_day: 3 },
          { purpose: 'screen', usd_per_day: 0.5 },
          { purpose: 'check', usd_per_day: 1 },
          { purpose: 'adjudicate', usd_per_day: 2 },
          { purpose: 'narrative', usd_per_day: 0.5 },
        ],
        runner: meta.runner,
        harness: meta.harness,
        sandbox: meta.sandbox,
        webhook_configured: true,
        models: { nano: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B', super: 'nvidia/nemotron-3-super-120b-a12b', ultra: 'nvidia/Nemotron-3-Ultra-550b-a55b' },
      }
      return delay(s)
    },
    async postDemoEvent(e) {
      if (import.meta.env.DEV) console.debug('[demo event]', e.kind, e.props)
    },
    openStream(since, handlers) {
      // Test hook: behave like a host at its SSE stream cap (429 rate_limited).
      const limited = typeof window !== 'undefined' && (window as unknown as { __RATCHET_FIXTURE_SSE_LIMITED__?: boolean }).__RATCHET_FIXTURE_SSE_LIMITED__
      if (limited) {
        queueMicrotask(() => handlers.onError?.({ rateLimited: true }))
        return { close: () => {} }
      }
      listeners.add(handlers)
      queueMicrotask(() => {
        handlers.onOpen?.()
        if (since != null) for (const e of events) if (e.id > since) handlers.onEvent(structuredClone(e))
      })
      return { close: () => listeners.delete(handlers) }
    },
  }
  return api
}
