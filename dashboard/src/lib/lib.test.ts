import { describe, expect, it } from 'vitest'
import { parseUnifiedDiff } from '../components/DiffViewer'
import { DIFF, STEPS_GOLDEN, STEPS_REPLAY } from '../kit/samples'
import { alignSteps, diffPaths } from './align'
import { referrerBucket } from '../analytics'
import { backoffDelay, BACKOFF_MAX_MS } from '../api/useEventStream'
import { shortDay, usd, usdTier } from './format'
import { customRange, editRange, fillDays, lastDays, presetRange, rangeInputs } from './range'

describe('parseUnifiedDiff', () => {
  it('splits files, counts changes and numbers lines', () => {
    const files = parseUnifiedDiff(DIFF)
    expect(files.map((f) => [f.path, f.status, f.additions, f.deletions])).toEqual([
      ['skills/github-triage/SKILL.md', 'modified', 3, 1],
      ['skills/github-triage/examples.md', 'added', 2, 0],
    ])
    const first = files[0]!.lines.find((l) => l.type === 'ctx')!
    expect([first.oldNo, first.newNo]).toEqual([9, 9])
  })
})

describe('alignSteps', () => {
  it('aligns matching steps and leaves a gap for a call only the replay made', () => {
    const rows = alignSteps(STEPS_GOLDEN, STEPS_REPLAY)
    const gap = rows.find((r) => r.golden === null)
    expect(gap?.replay?.kind === 'tool' && gap.replay.tool).toBe('github.close_issue')
    expect(rows[0]!.differs).toBe(false)
  })
})

describe('format', () => {
  it('formats money with its tier', () => {
    expect(usdTier(0.04, 'nano')).toBe('$0.040 (nano)')
    expect(usd(0.0042)).toBe('$0.0042')
    expect(usd(1.5)).toBe('$1.50')
  })
  it('reads a date-only string as a local calendar day', () => {
    expect(shortDay('2026-10-15')).toBe('15 Oct')
  })
})

describe('analytics', () => {
  it('buckets the referrer host only', () => {
    expect(referrerBucket('https://devpost.com/software/ratchet')).toBe('devpost')
    expect(referrerBucket('https://www.youtube.com/watch?v=1')).toBe('youtube')
    expect(referrerBucket('https://github.com/x/y')).toBe('github')
    expect(referrerBucket('https://news.ycombinator.com/')).toBe('other')
    expect(referrerBucket('')).toBe('none')
  })
})

describe('event stream backoff', () => {
  it('grows exponentially and caps at 30 s', () => {
    expect(backoffDelay(1, 0.5)).toBe(1000)
    expect(backoffDelay(3, 0.5)).toBe(4000)
    expect(backoffDelay(20, 0.5)).toBe(BACKOFF_MAX_MS)
  })
})

describe('diffPaths', () => {
  it('names the leaf that changed, including dropped fields', () => {
    const before = { sheet: 'Q4', rows: [{ vendor: 'Adlon', net: 312, vat: 59.28 }] }
    const after = { sheet: 'Q4', rows: [{ vendor: 'Adlon', net: 312 }] }
    expect(diffPaths(before, after)).toEqual([{ path: 'rows[0].vat', before: 59.28, after: undefined }])
  })
})

describe('cost date windows', () => {
  const now = new Date(2026, 9, 16, 15, 30)
  it('day presets snap to local midnight and end tomorrow', () => {
    const r = lastDays(14, now)
    expect(new Date(r.from)).toEqual(new Date(2026, 9, 3))
    expect(new Date(r.to)).toEqual(new Date(2026, 9, 17))
    expect(presetRange('today', now)).toEqual({ from: new Date(2026, 9, 16).toISOString(), to: new Date(2026, 9, 17).toISOString() })
    expect(rangeInputs(presetRange('7d', now))).toEqual({ from: '2026-10-10', to: '2026-10-16' })
  })
  it('custom ranges are inclusive days, from ≤ to, at most 366 days', () => {
    expect(customRange('2026-10-01', '2026-10-01')).toEqual({ from: new Date(2026, 9, 1).toISOString(), to: new Date(2026, 9, 2).toISOString() })
    expect(customRange('2026-10-05', '2026-10-01')).toBeNull()
    expect(customRange('2024-01-01', '2026-10-01')).toBeNull()
    expect(customRange('', '2026-10-01')).toBeNull()
  })
  it('the edit window runs from detection to a minute after the verdict', () => {
    expect(editRange('2026-10-16T10:00:00Z', '2026-10-16T10:01:00Z')).toEqual({ from: '2026-10-16T10:00:00.000Z', to: '2026-10-16T10:02:00.000Z' })
    expect(editRange('nope', '2026-10-16T10:01:00Z')).toBeNull()
  })
  it('fills quiet days so the chart shows every day of a short window', () => {
    const r = customRange('2026-10-01', '2026-10-03')!
    const out = fillDays([{ day: '2026-10-02', usd: 1 }], r, (day) => ({ day, usd: 0 }))
    expect(out).toEqual([
      { day: '2026-10-01', usd: 0 },
      { day: '2026-10-02', usd: 1 },
      { day: '2026-10-03', usd: 0 },
    ])
  })
})
