import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { CostPanel, savingsLine } from './CostPanel'

describe('CostPanel', () => {
  it('states what the cascade saved against the all-Ultra counterfactual', () => {
    render(<CostPanel usdByTier={{ nano: 0.005, super: 0.01, ultra: 0.025 }} tokensByTier={{ nano: 80_000, super: 12_000, ultra: 16_000 }} counterfactualUsd={0.4} />)
    expect(screen.getByTestId('cost-saved')).toHaveTextContent('cascade saved $0.360 (10×)')
    expect(screen.getByText('Nemotron Nano')).toBeInTheDocument()
    expect(screen.getByText((_, el) => el?.tagName === 'SPAN' && el.textContent === '80k tok')).toBeInTheDocument()
  })

  it('omits the line when nothing was saved', () => {
    expect(savingsLine(0.1, 0.1)).toBeNull()
    expect(savingsLine(0, 0.5)).toBeNull()
    expect(savingsLine(0.0118, 0.0814)).toBe('cascade saved $0.070 (6.9×)')
  })

  it('shows agent replay spend separately from judging, with its tier', () => {
    render(
      <CostPanel
        usdByTier={{ nano: 0.001, super: 0.009, ultra: 0.03 }}
        tokensByTier={{ nano: 10_000, super: 9_000, ultra: 9_000 }}
        counterfactualUsd={0.2}
        replayUsdByTier={{ super: 0.26 }}
      />,
    )
    expect(screen.getByText('Judging total')).toBeInTheDocument()
    expect(screen.getByTestId('replay-cost')).toHaveTextContent('$0.260 (super)')
  })

  it('hides the replay row when there was no replay spend', () => {
    render(<CostPanel usdByTier={{ nano: 0, super: 0, ultra: 0 }} tokensByTier={{ nano: 0, super: 0, ultra: 0 }} counterfactualUsd={0} />)
    expect(screen.queryByTestId('replay-cost')).toBeNull()
  })
})
