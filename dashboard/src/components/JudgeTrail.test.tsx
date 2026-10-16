import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { op } from '../kit/samples'
import { JudgeTrail } from './JudgeTrail'

const tiersInOrder = () =>
  within(screen.getByRole('group', { name: 'Judge trail' }))
    .getAllByRole('button')
    .map((b) => [b.getAttribute('data-tier'), b.hasAttribute('data-skipped')])

describe('JudgeTrail', () => {
  it('renders badges in cascade order whatever order the opinions arrive in', () => {
    render(<JudgeTrail opinions={[op('ultra', 'regressed', 0.95, 'u'), op('nano', 'regressed', 0.9, 'n'), op('super', 'regressed', 0.86, 's')]} />)
    expect(tiersInOrder()).toEqual([
      ['nano', false],
      ['super', false],
      ['ultra', false],
    ])
  })

  it('renders tiers that were not consulted as skipped', () => {
    render(<JudgeTrail opinions={[op('nano', 'equivalent', 0.95, 'same')]} />)
    expect(tiersInOrder()).toEqual([
      ['nano', false],
      ['super', true],
      ['ultra', true],
    ])
  })

  it('trails with a C marker (word + glyph) when a control replay decided (ADR 0023)', () => {
    const { rerender } = render(<JudgeTrail opinions={[op('nano', 'regressed', 0.7, 'x'), op('super', 'regressed', 0.66, 'y')]} decidedBy="control" />)
    const c = screen.getByRole('button', { name: 'Control replay: the unchanged skill also produced a different result' })
    expect(c).toHaveTextContent(/^C/)
    const buttons = within(screen.getByRole('group', { name: 'Judge trail' })).getAllByRole('button')
    expect(buttons.at(-1)).toBe(c)
    rerender(<JudgeTrail opinions={[op('nano', 'regressed', 0.7, 'x')]} decidedBy="super" />)
    expect(screen.queryByTestId('trail-control')).not.toBeInTheDocument()
  })

  it('leads with an L0 marker when Level 0 decided and trails with Tavily when drift was checked', () => {
    const { rerender } = render(<JudgeTrail opinions={[]} decidedBy="level0" />)
    expect(screen.getByRole('button', { name: 'Level 0: checked without a model' })).toBeInTheDocument()
    rerender(
      <JudgeTrail
        opinions={[op('nano', 'uncertain', 0.5, 'x')]}
        drift={[{ fact_id: 'f', query: '', tavily_results_ref: '', changed: true, old_value: '1', new_value: '2', confidence: 0.9, statement: '', source_url: null, source_title: null, status: 'checked', attributable: true, ruling: '' }]}
      />,
    )
    expect(screen.getByRole('button', { name: 'Tavily: a fact this task depends on changed.' })).toBeInTheDocument()
  })
})
