import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PAIRS } from '../kit/samples'
import { sortPairs } from '../lib/pairs'
import { renderUI } from '../test/utils'
import { PairGrid } from './PairGrid'

describe('PairGrid', () => {
  it('sorts regression, uncertain, (failed), drift, improved, pass', () => {
    expect(sortPairs(PAIRS).map((p) => p.outcome)).toEqual(['regression', 'regression', 'uncertain', 'failed', 'drift', 'improved', 'pass'])
  })

  it('renders rows in outcome order with the Level 0 copy and an Open link', () => {
    renderUI(<PairGrid pairs={PAIRS} verdictId="v1" forceLayout="table" />)
    const rows = within(screen.getByTestId('pair-grid')).getAllByRole('row').slice(1)
    expect(rows.map((r) => r.getAttribute('data-outcome'))).toEqual(['regression', 'regression', 'uncertain', 'failed', 'drift', 'improved', 'pass'])
    expect(screen.getAllByText('Checked without a model: 2 new write_irreversible calls.').length).toBeGreaterThan(0)
    expect(screen.getAllByRole('link', { name: /^Open / })).toHaveLength(PAIRS.length)
  })

  it('marks pairs with a control replay: agrees is neutral, diverges warns (ADR 0023)', () => {
    const agrees = { ...PAIRS[0]!, golden_run_id: 'g_a', control_run_id: 'ctl_a', control_outcome: 'agrees' as const }
    const held = { ...PAIRS[0]!, golden_run_id: 'g_d', outcome: 'pass' as const, decided_by: 'control' as const, control_run_id: 'ctl_d', control_outcome: 'diverges' as const, reason: 'The unchanged skill also produced a different result on this task, so the difference is nondeterminism, not the edit.' }
    const plain = { ...PAIRS[0]!, golden_run_id: 'g_p' }
    for (const layout of ['table', 'cards'] as const) {
      const { unmount } = renderUI(<PairGrid pairs={[agrees, held, plain]} verdictId="v1" forceLayout={layout} />)
      expect(screen.getByText('Control: agrees')).toHaveClass('tint-neutral')
      expect(screen.getByText('Control: diverges')).toHaveClass('tint-warn')
      expect(screen.getAllByTestId('trail-control')).toHaveLength(1)
      // "Same result" plus a visible "(noisy task)" qualifier, and the server's reason instead of the task label.
      const row = screen.getByTestId('noisy-task').closest('[data-outcome]')!
      expect(row).toHaveAttribute('data-outcome', 'pass')
      expect(within(row as HTMLElement).getByText('Same result')).toBeInTheDocument()
      expect(screen.getByTestId('noisy-task')).toHaveTextContent('(noisy task)')
      expect(within(row as HTMLElement).getByText(/so the difference is nondeterminism, not the edit\.$/)).toBeInTheDocument()
      unmount()
    }
  })

  it('renders 40 rows without pagination', () => {
    const many = Array.from({ length: 40 }, (_, i) => ({ ...PAIRS[0]!, golden_run_id: `g${i}`, replay_run_id: `r${i}` }))
    renderUI(<PairGrid pairs={many} verdictId="v1" forceLayout="table" />)
    expect(within(screen.getByTestId('pair-grid')).getAllByRole('row')).toHaveLength(41)
  })

  it('truncates the task at 80 characters and keeps the full text on hover', () => {
    const long = { ...PAIRS[0]!, task_input: 'x'.repeat(120) }
    renderUI(<PairGrid pairs={[long]} verdictId="v1" forceLayout="table" />)
    const cell = screen.getByTitle('x'.repeat(120))
    expect(cell.textContent).toHaveLength(80)
  })

  it('shows skeleton rows while loading and the empty-state copy when empty', () => {
    const { unmount } = renderUI(<PairGrid pairs={[]} verdictId="v1" loading />)
    expect(screen.getByLabelText('Loading pairs')).toBeInTheDocument()
    unmount()
    renderUI(<PairGrid pairs={[]} verdictId="v1" />)
    expect(screen.getByTestId('empty-state')).toHaveTextContent('No tasks were replayed for this edit.')
  })
})
