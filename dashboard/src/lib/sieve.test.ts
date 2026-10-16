import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createFixtureApi } from '../api/fixtures/index'
import type { Event, PairVerdict } from '../api/types'
import { PAIRS } from '../kit/samples'
import { dotPoint, eventsForVerdict, hopPath, layoutSieve, pathOffsets, routePoints, settledStage, sieveColumns, sieveGeometry, sieveTally, stageCounts, stagePitch } from './sieve'

let id = 0
const ev = (kind: string, payload: Record<string, unknown>, ref = 'e_1'): Event => ({ id: ++id, ts: new Date(0).toISOString(), kind, ref, payload: { edit_id: 'e_1', verdict_id: 'v_1', ...payload }, session_hash: null })

describe('layoutSieve: the event stream', () => {
  beforeEach(() => {
    id = 0
  })

  it('brings dots in at the left as replay batches report results', () => {
    const dots = layoutSieve([ev('batch.heartbeat', { batch_id: 'batch_a', k: 2, n: 4 }), ev('batch.heartbeat', { batch_id: 'batch_a', k: 3, n: 4 })], [])
    expect(dots.map((d) => [d.key, d.stage, d.slot, d.golden])).toEqual([
      ['d0', -1, 0, null],
      ['d1', -1, 1, null],
      ['d2', -1, 2, null],
    ])
  })

  it('ignores control batches (ctrl_…): they re-run contested pairs, not new tasks', () => {
    const dots = layoutSieve([ev('batch.heartbeat', { batch_id: 'batch_a', k: 2 }), ev('batch.heartbeat', { batch_id: 'ctrl_9f_a1_00', k: 4 })], [])
    expect(dots).toHaveLength(2)
  })

  it('hops one column per pair.level0 / pair.opinion and settles on pair.decided', () => {
    const events = [
      ev('batch.heartbeat', { batch_id: 'batch_a', k: 2 }),
      ev('pair.level0', { golden: 'g1', replay: 'r1', hard_fail: false }),
      ev('pair.opinion', { golden: 'g1', replay: 'r1', tier: 'nano', verdict: 'equivalent', confidence: 0.94, usd: 0.0006 }),
      ev('pair.decided', { golden: 'g1', replay: 'r1', outcome: 'pass' }),
      ev('pair.level0', { golden: 'g2', replay: 'r2', hard_fail: true }),
      ev('pair.decided', { golden: 'g2', replay: 'r2', outcome: 'regression' }),
    ]
    const [a, b] = layoutSieve(events, [])
    expect(a).toMatchObject({ key: 'd0', golden: 'g1', stage: 1, path: [-1, 0, 1], outcome: 'pass', settled: true, usd: 0.0006 })
    expect(a!.usdByTier).toEqual({ nano: 0.0006 })
    // The second anonymous dot becomes g2's dot: the key (and so the drawn element) carries over.
    expect(b).toMatchObject({ key: 'd1', golden: 'g2', stage: 0, path: [-1, 0], outcome: 'regression', settled: true })
  })

  it('never moves a dot backwards and keeps arrival order as the slot order', () => {
    const events = [
      ev('pair.level0', { golden: 'g1', replay: 'r1' }),
      ev('pair.level0', { golden: 'g2', replay: 'r2' }),
      ev('pair.opinion', { golden: 'g2', replay: 'r2', tier: 'super', usd: 0.002 }),
      ev('pair.opinion', { golden: 'g1', replay: 'r1', tier: 'super', usd: 0.002 }),
      ev('pair.opinion', { golden: 'g1', replay: 'r1', tier: 'nano', usd: 0.0005 }),
    ]
    const dots = layoutSieve(events, [])
    expect(dots.map((d) => [d.golden, d.stage, d.slot])).toEqual([
      ['g1', 2, 1],
      ['g2', 2, 0],
    ])
    expect(dots[0]!.usd).toBeCloseTo(0.0025)
  })

  it('sorts by event id, whatever order the events arrive in', () => {
    const a = ev('pair.level0', { golden: 'g1', replay: 'r1' })
    const b = ev('pair.opinion', { golden: 'g1', replay: 'r1', tier: 'nano', usd: 0 })
    expect(layoutSieve([b, a], [])[0]!.path).toEqual([-1, 0, 1])
  })

  it('selects only one verdict’s events', () => {
    const mine = ev('pair.level0', { golden: 'g1' })
    const other = { ...ev('pair.level0', { golden: 'g9' }), ref: 'e_2', payload: { edit_id: 'e_2', verdict_id: 'v_2', golden: 'g9' } }
    expect(eventsForVerdict([mine, other], 'v_1', 'e_1')).toEqual([mine])
  })
})

describe('layoutSieve: decided pairs', () => {
  it('settles each pair in the column that decided it, with the route it took', () => {
    const dots = layoutSieve([], PAIRS)
    const by = new Map(dots.map((d) => [d.golden, d]))
    for (const p of PAIRS) {
      const d = by.get(p.golden_run_id)!
      expect(d.settled).toBe(true)
      expect(d.outcome).toBe(p.outcome)
      expect(d.stage).toBe(settledStage(p))
      expect(d.path[0]).toBe(-1)
      expect(d.path.at(-1)).toBe(d.stage)
    }
    const l0 = PAIRS.find((p) => p.decided_by === 'level0')!
    expect(by.get(l0.golden_run_id)!.path).toEqual([-1, 0])
    const ultra = PAIRS.find((p) => p.decided_by === 'ultra')!
    expect(by.get(ultra.golden_run_id)!.path).toEqual([-1, 0, 1, 2, 3])
  })

  it('leaves a replay that never ran (infra) at the entry, decided as failed', () => {
    const failed: PairVerdict = { ...PAIRS[0]!, golden_run_id: 'g_f', replay_run_id: '', decided_by: 'infra', outcome: 'failed', opinions: [] }
    const [d] = layoutSieve([], [failed])
    expect(d).toMatchObject({ stage: -1, path: [-1], outcome: 'failed', settled: true })
  })

  it('counts rungs reached, spend per tier, and the decided tally', () => {
    const dots = layoutSieve([], PAIRS)
    const cols = sieveColumns(dots)
    expect(cols.map((c) => c.key)).toEqual(['level0', 'nano', 'super', 'ultra'])
    expect(cols[0]!.usd).toBe(0)
    const nanoSpend = PAIRS.flatMap((p) => p.opinions).filter((o) => o.tier === 'nano').reduce((a, o) => a + o.usd, 0)
    expect(cols[1]!.usd).toBeCloseTo(nanoSpend)
    for (let i = 1; i < 4; i++) expect(cols[i]!.reached).toBeLessThanOrEqual(cols[i - 1]!.reached)
    const tally = sieveTally(dots)
    expect(tally[0]![0]).toBe('regression')
    expect(tally.reduce((a, [, n]) => a + n, 0)).toBe(PAIRS.filter((p) => p.decided_by !== 'infra' || p.outcome).length)
  })
})

describe('layoutSieve on the simulated stream (fixture backend)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    ;(window as unknown as { __RATCHET_FIXTURE_SPEED__?: number }).__RATCHET_FIXTURE_SPEED__ = 4
  })
  afterEach(() => {
    vi.useRealTimers()
    delete (window as unknown as { __RATCHET_FIXTURE_SPEED__?: number }).__RATCHET_FIXTURE_SPEED__
  })

  async function run(scenario: string) {
    const api = createFixtureApi()
    const seen: Event[] = []
    api.openStream(null, { onEvent: (e) => seen.push(e) })
    const sim = api.simulate(scenario)
    await vi.advanceTimersByTimeAsync(400)
    const { verdict_id, edit_id } = await sim
    const snapshots: ReturnType<typeof layoutSieve>[] = []
    for (let i = 0; i < 40; i++) {
      await vi.advanceTimersByTimeAsync(400)
      snapshots.push(layoutSieve(eventsForVerdict(seen, verdict_id!, edit_id), []))
    }
    const view = await (async () => {
      const p = api.getVerdict(verdict_id!)
      await vi.advanceTimersByTimeAsync(200)
      return p
    })()
    return { events: eventsForVerdict(seen, verdict_id!, edit_id), pairs: view.verdict!.pairs, snapshots }
  }

  it('github-triage: every replay enters, six settle at Level 0 as regressions, the rest at Nano or Super', async () => {
    const { events, pairs, snapshots } = await run('github-triage')
    expect(events.some((e) => e.kind === 'verdict.decided')).toBe(true)
    // Mid-run there was a moment with unchecked dots at the entry.
    expect(snapshots.some((s) => s.some((d) => d.stage === -1))).toBe(true)
    const live = layoutSieve(events, [])
    const final = layoutSieve(events, pairs)
    expect(live).toHaveLength(pairs.length)
    // Freezing on the decided verdict does not move a single dot.
    expect(final.map((d) => [d.key, d.golden, d.stage, d.slot, d.outcome])).toEqual(live.map((d) => [d.key, d.golden, d.stage, d.slot, d.outcome]))
    expect(final.filter((d) => d.stage === 0 && d.outcome === 'regression')).toHaveLength(6)
    expect(final.every((d) => d.settled)).toBe(true)
    expect(final.filter((d) => d.stage === 1 || d.stage === 2).every((d) => d.outcome === 'pass')).toBe(true)
  })

  it('expense-report: dots that Ultra decided pass through every column; control rulings settle after the last judge', async () => {
    const { events, pairs } = await run('expense-report')
    const final = layoutSieve(events, pairs)
    const ultra = final.filter((d) => d.decidedBy === 'ultra')
    expect(ultra.length).toBeGreaterThan(0)
    for (const d of ultra) expect(d.path).toEqual([-1, 0, 1, 2, 3])
    const noisy = final.find((d) => d.decidedBy === 'control')!
    expect(noisy.outcome).toBe('pass')
    expect(noisy.stage).toBeGreaterThan(0)
  })
})

describe('geometry', () => {
  const g = sieveGeometry(960, 220)

  it('stacks dots down a column on the 16-px pitch and wraps into sub-columns', () => {
    const a = dotPoint(g, 1, 0)
    const b = dotPoint(g, 1, 1)
    expect(b.y - a.y).toBe(16)
    expect(b.x).toBe(a.x)
    const wrapped = dotPoint(g, 1, g.rows)
    expect(wrapped.x - a.x).toBe(16)
    expect(wrapped.y).toBe(a.y)
  })

  it('routes every hop on 45° or 90° segments', () => {
    const cases = [
      [dotPoint(g, -1, 3), dotPoint(g, 0, 0)],
      [dotPoint(g, 0, 5), dotPoint(g, 1, 1)],
      [dotPoint(g, 2, 0), dotPoint(g, 3, 6)],
      [dotPoint(g, 1, 2), dotPoint(g, 1, 1)],
      [{ x: 0, y: 0 }, { x: 10, y: 80 }],
    ] as const
    for (const [a, b] of cases) {
      const pts = hopPath(a, b)
      expect(pts[0]).toEqual(a)
      expect(pts.at(-1)).toEqual(b)
      for (let i = 1; i < pts.length; i++) {
        const dx = Math.abs(pts[i]!.x - pts[i - 1]!.x)
        const dy = Math.abs(pts[i]!.y - pts[i - 1]!.y)
        expect(dx === 0 || dy === 0 || Math.abs(dx - dy) < 1e-9).toBe(true)
      }
    }
  })

  it('tightens a crowded column, then mirrors lanes around its rail so no ring floats off it', () => {
    const rows = g.rows
    expect(stagePitch(g, rows)).toBe(16)
    expect(stagePitch(g, rows + 3)).toBeLessThan(16)
    expect(stagePitch(g, 100)).toBeCloseTo(16 * 0.7)
    const rail = g.colX[1]
    const lanes = new Set(Array.from({ length: 40 }, (_, i) => dotPoint(g, 1, i, 40).x - rail))
    expect([...lanes].sort((a, b) => a - b)).toEqual([-16, 0, 16, 32])
  })

  it('draws each settled route from the entry through every column it passed, on 45°/90° segments', () => {
    const dots = layoutSieve([], PAIRS)
    const counts = stageCounts(dots)
    const ultra = dots.find((d) => d.decidedBy === 'ultra')!
    const pts = routePoints(g, ultra, dots.length, counts)
    expect(pts[0]).toEqual(dotPoint(g, -1, ultra.index, dots.length))
    expect(pts.at(-1)).toEqual(dotPoint(g, 3, ultra.slot, counts.get(3)))
    for (const x of [g.colX[0], g.colX[1], g.colX[2]]) expect(pts.some((p) => p.x === x)).toBe(true)
    for (let i = 1; i < pts.length; i++) {
      const dx = Math.abs(pts[i]!.x - pts[i - 1]!.x)
      const dy = Math.abs(pts[i]!.y - pts[i - 1]!.y)
      expect(dx === 0 || dy === 0 || Math.abs(dx - dy) < 1e-9).toBe(true)
    }
    expect(routePoints(g, ultra, dots.length, counts, { fromRail: true })[0]).toEqual({ x: g.colX[0], y: pts[0]!.y })
    const never = dots.find((d) => d.stage === -1)!
    expect(routePoints(g, never, dots.length, counts)).toEqual([])
  })

  it('spaces keyframe offsets by segment length', () => {
    expect(pathOffsets([{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 30, y: 0 }])).toEqual([0, 1 / 3, 1])
  })
})
