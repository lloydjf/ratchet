/**
 * The cascade sieve, as data. `layoutSieve` folds a verdict's event stream (and, once the
 * verdict is decided, its pairs) into one dot per replayed task: where it is in the cascade
 * (Level 0 → Nano → Super → Ultra), the columns it passed through, and, once decided, its
 * outcome. Geometry is separate (`sieveGeometry`, `dotPoint`, `hopPath`) so the same layout
 * draws the full-width sieve and the timeline thumbnail.
 */
import type { DecidedBy, Event, PairOutcome, PairVerdict, Tier, TierMap } from '../api/types'
import { TIERS } from '../api/types'

/** -1: the replay finished but Level 0 has not run yet; 0..3: Level 0, Nano, Super, Ultra. */
export type SieveStage = -1 | 0 | 1 | 2 | 3
export const SIEVE_COLUMNS = ['level0', 'nano', 'super', 'ultra'] as const
export type SieveColumnKey = (typeof SIEVE_COLUMNS)[number]

export interface SieveDot {
  /** Stable across updates: `d<arrival index>`. */
  key: string
  index: number
  golden: string | null
  replay: string | null
  stage: SieveStage
  /** Stages visited in order, always starting at -1. */
  path: SieveStage[]
  /** Rank among the dots now in the same stage, in the order they arrived there. */
  slot: number
  outcome: PairOutcome | null
  settled: boolean
  decidedBy: DecidedBy | null
  task: string
  usd: number
  usdByTier: TierMap<number>
  drift: boolean
}

const TIER_STAGE: Record<Tier, SieveStage> = { nano: 1, super: 2, ultra: 3 }
const REPLAY_BATCH_EVENTS = new Set(['batch.submitted', 'batch.heartbeat', 'batch.finished'])

const str = (v: unknown): string | null => (typeof v === 'string' && v ? v : null)
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

/** Where a decided pair settles: the stage that decided it (drift and control rulings sit after the last judge). */
export function settledStage(p: Pick<PairVerdict, 'decided_by' | 'opinions'>): SieveStage {
  switch (p.decided_by) {
    case 'level0':
      return 0
    case 'nano':
      return 1
    case 'super':
      return 2
    case 'ultra':
      return 3
    case 'infra':
      return -1
    default: {
      const last = p.opinions.reduce<SieveStage>((acc, o) => (TIER_STAGE[o.tier] > acc ? TIER_STAGE[o.tier] : acc), 0)
      return last
    }
  }
}

function blankDot(index: number): SieveDot {
  return { key: `d${index}`, index, golden: null, replay: null, stage: -1, path: [-1], slot: 0, outcome: null, settled: false, decidedBy: null, task: '', usd: 0, usdByTier: {}, drift: false }
}

/**
 * Fold events (any order; sorted by id here) and the decided pairs (empty while running)
 * into dots. Replay batches (`batch.*`, never the `ctrl_` control batches) bring anonymous
 * dots in at the left; `pair.level0`, `pair.opinion` and `pair.decided` move a task's dot
 * one column at a time and settle it. Pairs, when given, are the final word.
 */
export function layoutSieve(events: readonly Event[], pairs: readonly PairVerdict[]): SieveDot[] {
  const dots: SieveDot[] = []
  const byGolden = new Map<string, SieveDot>()
  const batchK = new Map<string, number>()
  /** Arrival sequence into the dot's current stage, for slot order. */
  const entrySeq = new Map<SieveDot, number>()
  let seq = 0

  const claim = (golden: string, replay: string | null): SieveDot => {
    const hit = byGolden.get(golden)
    if (hit) {
      if (replay && !hit.replay) hit.replay = replay
      return hit
    }
    // The first anonymous dot the batches brought in becomes this task's dot.
    const anon = dots.find((d) => d.golden == null)
    const d = anon ?? blankDot(dots.length)
    if (!anon) {
      dots.push(d)
      entrySeq.set(d, seq++)
    }
    d.golden = golden
    d.replay = replay
    byGolden.set(golden, d)
    return d
  }
  const move = (d: SieveDot, to: SieveStage) => {
    if (to <= d.stage) return
    d.path.push(to)
    d.stage = to
    entrySeq.set(d, seq++)
  }

  for (const e of [...events].sort((a, b) => a.id - b.id)) {
    const p = e.payload
    if (REPLAY_BATCH_EVENTS.has(e.kind)) {
      const batch = str(p.batch_id) ?? e.ref
      if (batch.startsWith('ctrl_')) continue
      batchK.set(batch, Math.max(batchK.get(batch) ?? 0, num(p.k)))
      const entered = [...batchK.values()].reduce((a, b) => a + b, 0)
      while (dots.length < entered) {
        const d = blankDot(dots.length)
        dots.push(d)
        entrySeq.set(d, seq++)
      }
      continue
    }
    const golden = str(p.golden)
    if (!golden) continue
    const replay = str(p.replay)
    switch (e.kind) {
      case 'pair.level0':
        move(claim(golden, replay), 0)
        break
      case 'pair.opinion': {
        const tier = str(p.tier) as Tier | null
        if (!tier || !(tier in TIER_STAGE)) break
        const d = claim(golden, replay)
        move(d, TIER_STAGE[tier])
        d.usdByTier[tier] = (d.usdByTier[tier] ?? 0) + num(p.usd)
        d.usd += num(p.usd)
        break
      }
      case 'pair.drift':
        claim(golden, replay).drift = true
        break
      case 'pair.decided': {
        const d = claim(golden, replay)
        const outcome = str(p.outcome) as PairOutcome | null
        if (outcome) {
          d.outcome = outcome
          d.settled = true
        }
        break
      }
      default:
        break
    }
  }

  // The decided verdict is the final word: every pair has a dot, settled where it was decided.
  for (const p of pairs) {
    const d = claim(p.golden_run_id, p.replay_run_id || null)
    const final = settledStage(p)
    if (d.path.length === 1 && final >= 0) {
      // No events for this pair (a page opened after the run): rebuild the route it took.
      const visited = new Set<SieveStage>([0, ...p.opinions.map((o) => TIER_STAGE[o.tier])])
      for (const s of [0, 1, 2, 3] as SieveStage[]) if (s <= final && visited.has(s)) d.path.push(s)
      if (d.path.at(-1) !== final) d.path.push(final)
      d.stage = final
      entrySeq.set(d, seq++)
    } else if (final > d.stage) move(d, final)
    d.outcome = p.outcome
    d.settled = true
    d.decidedBy = p.decided_by
    d.task = p.task_label || p.task_input
    d.drift = d.drift || p.drift.length > 0
    const byTier: TierMap<number> = {}
    for (const o of p.opinions) byTier[o.tier] = (byTier[o.tier] ?? 0) + o.usd
    d.usdByTier = byTier
    d.usd = p.usd
  }

  // Slots: rank within the current stage by arrival there.
  const groups = new Map<SieveStage, SieveDot[]>()
  for (const d of dots) groups.set(d.stage, [...(groups.get(d.stage) ?? []), d])
  for (const g of groups.values()) {
    g.sort((a, b) => (entrySeq.get(a) ?? 0) - (entrySeq.get(b) ?? 0))
    g.forEach((d, i) => (d.slot = i))
  }
  return dots
}

export interface SieveColumnStat {
  key: SieveColumnKey
  /** Tasks that reached this rung (passed through or stopped here). */
  reached: number
  /** Tasks decided here. */
  decided: number
  usd: number
}

/** Per-column rung counts and spend (Level 0 is model-free). */
export function sieveColumns(dots: readonly SieveDot[]): SieveColumnStat[] {
  return SIEVE_COLUMNS.map((key, i) => {
    const stage = i as SieveStage
    const reached = dots.filter((d) => d.path.includes(stage)).length
    const decided = dots.filter((d) => d.settled && d.stage === stage).length
    const tier = i === 0 ? null : TIERS[i - 1]!
    const usd = tier ? dots.reduce((a, d) => a + (d.usdByTier[tier] ?? 0), 0) : 0
    return { key, reached, decided, usd }
  })
}

/** Decided tasks per outcome, in problems-first order. */
export function sieveTally(dots: readonly SieveDot[]): [PairOutcome, number][] {
  const order: PairOutcome[] = ['regression', 'uncertain', 'failed', 'drift', 'improved', 'pass']
  const m = new Map<PairOutcome, number>()
  for (const d of dots) if (d.settled && d.outcome) m.set(d.outcome, (m.get(d.outcome) ?? 0) + 1)
  return order.filter((o) => m.get(o)).map((o) => [o, m.get(o)!])
}

/** The events that belong to one verdict's run (the backend stamps verdict_id and edit_id on each). */
export function eventsForVerdict(events: readonly Event[], verdictId: string, editId: string | null | undefined): Event[] {
  return events.filter((e) => e.payload.verdict_id === verdictId || (!!editId && (e.payload.edit_id === editId || e.ref === editId)))
}

// ---------------------------------------------------------------- geometry
export interface SieveGeometry {
  width: number
  height: number
  /** Left edge and width of the entry gutter (replays not yet checked). */
  entryX: number
  /** x of each column's line (Level 0, Nano, Super, Ultra). */
  colX: [number, number, number, number]
  colWidth: number
  fieldTop: number
  fieldBottom: number
  pitch: number
  rows: number
}

export function sieveGeometry(width: number, height: number, opts: { pitch?: number; top?: number; bottom?: number; entry?: number; inset?: number } = {}): SieveGeometry {
  const pitch = opts.pitch ?? 16
  const fieldTop = opts.top ?? 40
  const fieldBottom = height - (opts.bottom ?? 56)
  const entry = opts.entry ?? 56
  const inset = opts.inset ?? 16
  const colWidth = Math.max(pitch * 2, (width - entry - inset) / 4)
  const colX = [0, 1, 2, 3].map((i) => entry + i * colWidth + inset) as SieveGeometry['colX']
  const rows = Math.max(1, Math.floor((fieldBottom - fieldTop) / pitch))
  return { width, height, entryX: inset, colX, colWidth, fieldTop, fieldBottom, pitch, rows }
}

export interface Point {
  x: number
  y: number
}

/** The pitch a stage uses for `count` dots: the base pitch, tightened (down to 70 %) so a full column stays on its rail. */
export function stagePitch(g: SieveGeometry, count?: number): number {
  const h = g.fieldBottom - g.fieldTop
  if (!count || count * g.pitch <= h) return g.pitch
  return Math.max(g.pitch * 0.7, h / count)
}

/**
 * Centre of a dot: stage column, stacked down from the top. A column that still overflows
 * after tightening mirrors extra lanes around its rail (right, left, right…); the entry
 * gutter only grows to the right.
 */
export function dotPoint(g: SieveGeometry, stage: SieveStage, slot: number, count?: number): Point {
  const pitch = stagePitch(g, count)
  const rows = Math.max(1, Math.floor((g.fieldBottom - g.fieldTop) / pitch))
  const lane = Math.floor(slot / rows)
  const row = slot % rows
  const baseX = stage < 0 ? g.entryX : g.colX[stage as 0 | 1 | 2 | 3]
  const maxLanes = Math.max(1, Math.floor((stage < 0 ? g.colX[0] - g.entryX - g.pitch : g.colWidth - g.pitch) / g.pitch) + 1)
  const k = Math.min(lane, maxLanes - 1)
  const offset = stage < 0 ? k : k === 0 ? 0 : k % 2 === 1 ? Math.ceil(k / 2) : -Math.ceil(k / 2)
  return { x: baseX + offset * g.pitch, y: g.fieldTop + pitch / 2 + row * pitch }
}

/** How many dots each stage holds (for `dotPoint`'s pitch). */
export function stageCounts(dots: readonly SieveDot[]): Map<SieveStage, number> {
  const m = new Map<SieveStage, number>()
  for (const d of dots) m.set(d.stage, (m.get(d.stage) ?? 0) + 1)
  return m
}

/**
 * The route a settled dot took, as one 45°/90° polyline: from where it entered, along its
 * row through every column it passed, then into its berth.
 */
export function routePoints(g: SieveGeometry, d: SieveDot, total: number, counts: Map<SieveStage, number>, opts: { fromRail?: boolean } = {}): Point[] {
  if (d.stage < 0) return []
  const entry = dotPoint(g, -1, d.index, total)
  // Small drawings start at the Level 0 rail: the entry segments would only hatch the gutter.
  const start = opts.fromRail ? { x: g.colX[0], y: entry.y } : entry
  const end = dotPoint(g, d.stage, d.slot, counts.get(d.stage))
  const pts: Point[] = [start]
  for (const s of d.path.slice(1, -1)) {
    if (s < 0) continue
    const station = { x: g.colX[s as 0 | 1 | 2 | 3], y: start.y }
    pts.push(...hopPath(pts.at(-1)!, station).slice(1))
  }
  pts.push(...hopPath(pts.at(-1)!, end).slice(1))
  return pts
}

/**
 * The route between two stations on 45° and 90° segments only: straight out, then a
 * 45° run into the berth; vertical first when the drop is steeper than the run.
 */
export function hopPath(a: Point, b: Point): Point[] {
  const dx = b.x - a.x
  const dy = b.y - a.y
  if (dx === 0 || dy === 0) return [a, b]
  const ax = Math.abs(dx)
  const ay = Math.abs(dy)
  if (ay <= ax) return [a, { x: b.x - Math.sign(dx) * ay, y: a.y }, b]
  return [a, { x: a.x, y: b.y - Math.sign(dy) * ax }, b]
}

const len = (a: Point, b: Point) => Math.hypot(b.x - a.x, b.y - a.y)

/** Keyframe offsets (0..1) for a polyline, proportional to segment length. */
export function pathOffsets(points: readonly Point[]): number[] {
  const segs = points.slice(1).map((p, i) => len(points[i]!, p))
  const total = segs.reduce((a, b) => a + b, 0) || 1
  let acc = 0
  return [0, ...segs.map((s) => (acc += s) / total)]
}
