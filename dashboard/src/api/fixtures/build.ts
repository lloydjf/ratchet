/**
 * Builders for fixture objects. Dev-only (VITE_FIXTURES=1); never imported by app code
 * outside `api/select.ts`'s guarded dynamic import.
 */
import type {
  DecidedBy,
  DriftCheck,
  JudgeOpinion,
  LLMCall,
  Level0Finding,
  OpinionVerdict,
  PairOutcome,
  PairVerdict,
  SideEffectClass,
  Step,
  Tier,
  TierMap,
  ToolCall,
  Trace,
  WorldFact,
} from '../types'

export const PRICES: Record<Tier, { in: number; out: number }> = {
  nano: { in: 0.06, out: 0.24 },
  super: { in: 0.3, out: 0.9 },
  ultra: { in: 1.0, out: 3.0 },
}

export const MODEL_ID: Record<Tier, string> = {
  nano: 'nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B',
  super: 'nvidia/nemotron-3-super-120b-a12b',
  ultra: 'nvidia/Nemotron-3-Ultra-550b-a55b',
}

export function price(tier: Tier, promptTokens: number, completionTokens: number): number {
  const p = PRICES[tier]
  return (promptTokens * p.in + completionTokens * p.out) / 1_000_000
}

/** Small deterministic hash → hex string (fixtures only). */
export function hex(input: string, len = 12): string {
  let h1 = 0x811c9dc5
  let h2 = 0x1b873593
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i)
    h1 = Math.imul(h1 ^ c, 0x01000193)
    h2 = Math.imul(h2 ^ c, 0x5bd1e995)
  }
  const s = (h1 >>> 0).toString(16).padStart(8, '0') + (h2 >>> 0).toString(16).padStart(8, '0')
  return (s + s.split('').reverse().join('')).slice(0, len)
}

/** Seeded PRNG so fixture numbers are stable between reloads. */
export function rng(seed: string): () => number {
  let a = parseInt(hex(seed, 8), 16) || 1
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

export const isoAt = (base: number, offsetSeconds: number) => new Date(base + offsetSeconds * 1000).toISOString()

export class StepFactory {
  private m = 0
  private t = 0
  private clock: number
  private readonly runId: string

  constructor(runId: string, startMs: number) {
    this.runId = runId
    this.clock = startMs
  }

  private tick(seconds: number): string {
    this.clock += seconds * 1000
    return new Date(this.clock).toISOString()
  }

  llm(tier: Tier, opts: { preview: string; tools?: string[]; pt?: number; ct?: number; latency?: number }): LLMCall {
    this.m += 1
    const step_id = `m${String(this.m).padStart(3, '0')}`
    const pt = opts.pt ?? 4200
    const ct = opts.ct ?? 380
    return {
      kind: 'llm',
      step_id,
      tier,
      model: MODEL_ID[tier],
      request_hash: hex(`${this.runId}:${step_id}:req`, 64),
      messages_ref: `traces/${this.runId}/requests/${step_id}.json`,
      response_ref: `traces/${this.runId}/responses/${step_id}.json`,
      prompt_tokens: pt,
      cached_prompt_tokens: 0,
      completion_tokens: ct,
      usd: price(tier, pt, ct),
      latency_ms: opts.latency ?? 1800,
      started_at: this.tick(2),
      purpose: 'agent',
      api: 'chat',
      usage_estimated: false,
      tool_calls_requested: opts.tools ?? [],
      content_preview: opts.preview.slice(0, 400),
    }
  }

  tool(
    tool: string,
    args: Record<string, unknown>,
    side: SideEffectClass,
    opts: { result: string; blocked?: boolean; source?: ToolCall['source'] },
  ): ToolCall {
    this.t += 1
    const step_id = `t${String(this.t).padStart(3, '0')}`
    const args_hash = hex(`${tool}:${JSON.stringify(args)}`, 64)
    return {
      kind: 'tool',
      step_id,
      tool,
      args_norm: sortKeys(args),
      args_hash,
      result_hash: opts.blocked ? null : hex(`${tool}:${opts.result}`, 64),
      side_effect: side,
      source: opts.source ?? 'cassette',
      cassette_key: opts.blocked ? null : `${tool}/${args_hash.slice(0, 16)}`,
      result_preview: opts.result.slice(0, 400),
      blocked: opts.blocked ?? false,
      started_at: this.tick(1),
    }
  }
}

function sortKeys(o: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.keys(o)
    .sort()
    .map((k) => [k, o[k]]))
}

export interface TraceInput {
  runId: string
  kind: Trace['kind']
  taskInput: string
  taskLabel: string
  steps: Step[]
  finalOutput: string
  skills: string[]
  createdAt: string
  worldDependent?: boolean
  worldFacts?: WorldFact[]
  goldenOf?: string | null
  candidateCommit?: string | null
  criteria?: string[]
  blessedAt?: string | null
  skillsCommit: string
  seed?: number
}

export function makeTrace(t: TraceInput): Trace {
  return {
    run_id: t.runId,
    kind: t.kind,
    task_input: t.taskInput,
    steps: t.steps,
    final_output: t.finalOutput,
    artifacts_ref: null,
    skills_invoked: t.skills,
    world_dependent: t.worldDependent ?? false,
    world_facts: t.worldFacts ?? [],
    env: {
      workspace_ref: `workspaces/${t.runId}.tar.zst`,
      frozen_time: t.createdAt,
      harness_version: 'hermes/0.9.2',
      skills_commit: t.skillsCommit,
      world_ref: 'worlds/demo',
    },
    agent_tier: 'super',
    seed: t.seed ?? 7,
    created_at: t.createdAt,
    golden_of: t.goldenOf ?? null,
    candidate_commit: t.candidateCommit ?? null,
    success_criteria: t.criteria ?? [],
    archived_reason: null,
    blessed_at: t.blessedAt ?? null,
    harness_ok: true,
    error_code: null,
    task_label: t.taskLabel,
  }
}

export function opinion(
  tier: Tier,
  verdict: OpinionVerdict,
  confidence: number,
  reasons: string[],
  opts: {
    evidence?: string[]
    pt?: number
    ct?: number
    truncated?: boolean
    causal?: string
    driftFact?: string
    criteria?: { criterion: string; met: boolean; evidence_steps?: string[] }[]
  } = {},
): JudgeOpinion {
  const defaults: Record<Tier, [number, number]> = { nano: [7800, 420], super: [12400, 960], ultra: [16200, 1900] }
  const [pt, ct] = [opts.pt ?? defaults[tier][0], opts.ct ?? defaults[tier][1]]
  return {
    tier,
    verdict,
    confidence,
    reasons,
    evidence_steps: opts.evidence ?? [],
    usd: price(tier, pt, ct),
    prompt_hash: hex(`${tier}:prompt`, 12),
    truncated: opts.truncated ?? false,
    criteria_met: (opts.criteria ?? []).map((c) => ({ ...c, evidence_steps: c.evidence_steps ?? [] })),
    causal_explanation: opts.causal ?? null,
    possible_drift_fact: opts.driftFact ?? null,
    prompt_tokens: pt,
    completion_tokens: ct,
  }
}

/**
 * ADR 0024 `output_similarity`, for fixtures: whitespace collapsed, case folded, then
 * 2·LCS / (|a| + |b|), close to `difflib.SequenceMatcher.ratio()` (1.0 when both are empty).
 */
export function outputSimilarity(a: string, b: string): number {
  const norm = (s: string) => s.replace(/\s+/g, ' ').trim().toLowerCase()
  const x = norm(a)
  const y = norm(b)
  if (!x.length && !y.length) return 1
  if (x === y) return 1
  let prev = new Array<number>(y.length + 1).fill(0)
  for (let i = 1; i <= x.length; i++) {
    const cur = new Array<number>(y.length + 1).fill(0)
    for (let j = 1; j <= y.length; j++) cur[j] = x[i - 1] === y[j - 1] ? prev[j - 1]! + 1 : Math.max(prev[j]!, cur[j - 1]!)
    prev = cur
  }
  return (2 * prev[y.length]!) / (x.length + y.length)
}

export function level0(golden: Trace, replay: Trace): Level0Finding {
  const key = (s: ToolCall) => `${s.tool}:${s.args_hash}`
  const gTools = golden.steps.filter((s): s is ToolCall => s.kind === 'tool' && s.side_effect !== 'read')
  const rTools = replay.steps.filter((s): s is ToolCall => s.kind === 'tool' && s.side_effect !== 'read')
  const gCount = new Map<string, number>()
  for (const s of gTools) gCount.set(key(s), (gCount.get(key(s)) ?? 0) + 1)
  const unmatched: ToolCall[] = []
  for (const s of rTools) {
    const c = gCount.get(key(s)) ?? 0
    if (c > 0) gCount.set(key(s), c - 1)
    else unmatched.push(s)
  }
  const missing: ToolCall[] = gTools.filter((s) => {
    const c = gCount.get(key(s)) ?? 0
    if (c > 0) {
      gCount.set(key(s), c - 1)
      return true
    }
    return false
  })
  const changed: [ToolCall, ToolCall][] = []
  const newCalls: ToolCall[] = []
  const missingLeft = [...missing]
  for (const r of unmatched) {
    const idx = missingLeft.findIndex((g) => g.tool === r.tool)
    if (idx >= 0) {
      changed.push([missingLeft[idx]!, r])
      missingLeft.splice(idx, 1)
    } else newCalls.push(r)
  }
  const HARD = new Set(['destructive', 'write_irreversible', 'external_send', 'spend'])
  return {
    golden_run_id: golden.run_id,
    replay_run_id: replay.run_id,
    new_calls: newCalls,
    missing_calls: missingLeft,
    changed_args: changed,
    hard_fail: newCalls.some((c) => HARD.has(c.side_effect)),
    missing_hard: missingLeft.some((c) => c.side_effect === 'write_irreversible' || c.side_effect === 'external_send'),
    output_similarity: outputSimilarity(golden.final_output, replay.final_output),
  }
}

export function makePair(
  golden: Trace,
  replay: Trace,
  outcome: PairOutcome,
  decidedBy: DecidedBy,
  reason: string,
  opinions: JudgeOpinion[],
  drift: DriftCheck[] = [],
): PairVerdict {
  return {
    golden_run_id: golden.run_id,
    replay_run_id: replay.run_id,
    level0: level0(golden, replay),
    opinions,
    drift,
    outcome,
    usd: opinions.reduce((a, o) => a + o.usd, 0),
    error_code: null,
    task_input: golden.task_input,
    task_label: golden.task_label,
    decided_by: decidedBy,
    reason,
  }
}

/** Tokens of the Nano screen that judges one control replay (ADR 0023). */
export const CONTROL_NANO = { pt: 6800, ct: 260 }

export interface CostRollup {
  cost: TierMap<number>
  tokens: TierMap<number>
  counterfactual: number
}

/** Per-verdict cost by tier (judges + narrative) and the all-Ultra counterfactual. */
export function rollup(pairs: PairVerdict[], narrative: { pt: number; ct: number }): CostRollup {
  const cost: TierMap<number> = { nano: 0, super: 0, ultra: 0 }
  const toks: TierMap<number> = { nano: 0, super: 0, ultra: 0 }
  let cf = 0
  for (const p of pairs)
    for (const o of p.opinions) {
      cost[o.tier] = (cost[o.tier] ?? 0) + o.usd
      toks[o.tier] = (toks[o.tier] ?? 0) + o.prompt_tokens + o.completion_tokens
      cf += price('ultra', o.prompt_tokens, o.completion_tokens)
    }
  // ADR 0023: a control replay's Nano screen is part of the pair's cost.
  for (const p of pairs)
    if (p.control_run_id) {
      cost.nano = (cost.nano ?? 0) + price('nano', CONTROL_NANO.pt, CONTROL_NANO.ct)
      toks.nano = (toks.nano ?? 0) + CONTROL_NANO.pt + CONTROL_NANO.ct
    }
  const n = price('ultra', narrative.pt, narrative.ct)
  cost.ultra = (cost.ultra ?? 0) + n
  toks.ultra = (toks.ultra ?? 0) + narrative.pt + narrative.ct
  // Pairs Level 0 decided without a model still count in the counterfactual: an all-Ultra
  // judge would have read them too (Nano-sized context, Ultra prices).
  for (const p of pairs) if (p.opinions.length === 0) cf += price('ultra', 7800, 420)
  cf += n
  return { cost, tokens: toks, counterfactual: cf }
}
