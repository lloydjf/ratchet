import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PAIRS } from '../kit/samples'
import { hunkPairs, hunksOf, attributable } from '../lib/hunks'
import { pairKey } from '../lib/pairs'
import { matches } from './CommandPalette'
import { parseUnifiedDiff } from './DiffViewer'
import { TrailGlyph, trailStages } from './TrailGlyph'

describe('TrailGlyph', () => {
  it('fills up to the deciding stage and names every stage for assistive tech', () => {
    const ultra = PAIRS.find((p) => p.decided_by === 'ultra')!
    expect(trailStages(ultra)).toEqual({ consulted: [true, true, true, true], deciding: 3 })
    const l0 = PAIRS.find((p) => p.decided_by === 'level0')!
    expect(trailStages(l0).deciding).toBe(0)
    render(<TrailGlyph p={l0} />)
    expect(screen.getByTestId('trail-glyph')).toHaveTextContent('Level 0 checked, Nano not needed, Super not needed, Ultra not needed. Decided by Level 0.')
  })

  it('says a replay that never ran decided nothing', () => {
    const failed = PAIRS.find((p) => p.decided_by === 'infra')!
    expect(trailStages(failed).deciding).toBe(-1)
  })
})

describe('diff hunks link to the pairs they affected', () => {
  const diff = `diff --git a/skills/x/SKILL.md b/skills/x/SKILL.md
--- a/skills/x/SKILL.md
+++ b/skills/x/SKILL.md
@@ -1,2 +1,2 @@
-Label issues.
+Close stale issues with \`github.close_issue\`.
@@ -9,2 +9,2 @@
-Keep the summary short.
+Keep the summary under 150 words.
`
  const hunks = hunksOf(parseUnifiedDiff(diff))

  it('splits a patch into hunks with stable ids', () => {
    expect(hunks.map((h) => h.id)).toEqual(['skills/x/SKILL.md#0', 'skills/x/SKILL.md#1'])
  })

  it('attributes a pair to the hunk its evidence names, and never attributes drift', () => {
    const map = hunkPairs(hunks, PAIRS)
    const closer = PAIRS.find((p) => p.level0.new_calls.some((c) => c.tool === 'github.close_issue'))!
    expect(map.get('skills/x/SKILL.md#0')).toContain(pairKey(closer))
    const drift = PAIRS.find((p) => p.outcome === 'drift')!
    expect(attributable(drift)).toBe(false)
    for (const keys of map.values()) expect(keys).not.toContain(pairKey(drift))
  })

  it('gives a single-hunk diff every difference the edit could have caused', () => {
    const one = hunksOf(parseUnifiedDiff(diff.split('@@ -9')[0]!))
    expect(hunkPairs(one, PAIRS).get(one[0]!.id)).toEqual(PAIRS.filter(attributable).map(pairKey))
  })
})

describe('command palette matching', () => {
  it('needs every word of the query', () => {
    const c = { label: 'Blocked. expense-report', hint: '2 d ago', keywords: 'BLOCK v_rec_er', group: 'Verdicts' }
    expect(matches(c, 'expense block')).toBe(true)
    expect(matches(c, 'expense passed')).toBe(false)
  })
})
