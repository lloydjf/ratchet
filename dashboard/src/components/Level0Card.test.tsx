import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { DecidedBy, Level0Finding, PairVerdict } from '../api/types'
import { tool } from '../kit/samples'
import { nanoGatedByLevel0 } from '../lib/pairs'
import { Level0Card } from './Level0Card'

const label = (issue: number) => tool('t002', 'github.label', 'write_reversible', { repo: 'acme/widgets', issue, labels: ['p2'] }, '{"ok": true}')

function finding(extra: Partial<Level0Finding> = {}): Level0Finding {
  return { golden_run_id: 'g', replay_run_id: 'r', new_calls: [], missing_calls: [], changed_args: [], hard_fail: false, missing_hard: false, output_similarity: 1, ...extra }
}
const pair = (decided_by: DecidedBy, level0: Level0Finding) => ({ decided_by, level0 }) as Pick<PairVerdict, 'decided_by' | 'level0'>

describe('Level0Card final output similarity (ADR 0024)', () => {
  it('shows identical outputs on a pair with no action change', () => {
    render(<Level0Card finding={finding()} decidedBy="nano" outcome="pass" />)
    expect(screen.getByTestId('level0-card')).toHaveTextContent('The replay made the same side-effect calls as the original run.')
    expect(screen.getByTestId('level0-output')).toHaveTextContent('Final output: identical')
    expect(screen.queryByTestId('level0-nano-gated')).toBeNull()
  })

  it('shows a percentage when the outputs differ', () => {
    render(<Level0Card finding={finding({ output_similarity: 0.934 })} decidedBy="drift" outcome="drift" />)
    expect(screen.getByTestId('level0-output')).toHaveTextContent('Final output: 93 % similar')
    expect(screen.queryByTestId('level0-nano-gated')).toBeNull()
  })

  it('notes that Nano could not clear a pair alone when Level 0 found a re-argued call', () => {
    render(<Level0Card finding={finding({ changed_args: [[label(1), label(2)]] })} decidedBy="super" outcome="pass" />)
    expect(screen.getByTestId('level0-output')).toHaveTextContent('Final output: identical')
    expect(screen.getByTestId('level0-nano-gated')).toHaveTextContent('Nano could not clear this pair alone (Level 0 found differences).')
  })

  it('notes it too when the outputs are under 90 % alike', () => {
    render(<Level0Card finding={finding({ output_similarity: 0.82 })} decidedBy="ultra" outcome="regression" />)
    expect(screen.getByTestId('level0-output')).toHaveTextContent('Final output: 82 % similar')
    expect(screen.getByTestId('level0-nano-gated')).toBeInTheDocument()
  })

  it('has no output line on a pair that could not be replayed', () => {
    const { container } = render(<Level0Card finding={finding({ output_similarity: 0 })} decidedBy="infra" outcome="failed" />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('nanoGatedByLevel0', () => {
  it('holds only when a later stage decided a pair Level 0 found differences on', () => {
    const differs = finding({ new_calls: [label(3)] })
    expect(nanoGatedByLevel0(pair('super', differs))).toBe(true)
    expect(nanoGatedByLevel0(pair('control', finding({ output_similarity: 0.89 })))).toBe(true)
    expect(nanoGatedByLevel0(pair('ultra', finding({ output_similarity: 0.9 })))).toBe(false)
    expect(nanoGatedByLevel0(pair('nano', differs))).toBe(false)
    // Level 0 itself decided, or no judge ran: Nano never had the pair to clear.
    expect(nanoGatedByLevel0(pair('level0', differs))).toBe(false)
    expect(nanoGatedByLevel0(pair('infra', differs))).toBe(false)
  })
})
