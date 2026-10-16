import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { DecisionChip } from './DecisionChip'
import { OutcomeChip } from './OutcomeChip'
import { TierBadge } from './TierBadge'

describe('DecisionChip', () => {
  it.each([
    ['BLOCK', 'Blocked'],
    ['WARN', 'Held'],
    ['PASS', 'Passed'],
  ] as const)('labels %s as "%s" with a drawn mark (no Unicode glyph)', (decision, word) => {
    render(<DecisionChip decision={decision} />)
    const chip = screen.getByRole('img', { name: `Decision: ${decision}` })
    expect(chip).toHaveTextContent(new RegExp(`^${word}$`))
    expect(chip.querySelector('svg')).not.toBeNull()
    expect(chip).toHaveAttribute('data-decision', decision)
  })

  it('says "Expected" when muted so an unrun scenario is not read as a verdict', () => {
    render(<DecisionChip decision="BLOCK" muted />)
    expect(screen.getByRole('img', { name: 'Expected decision: BLOCK' })).toBeInTheDocument()
  })
})

describe('OutcomeChip', () => {
  it.each([
    ['regression', 'Got worse'],
    ['drift', 'World changed'],
    ['pass', 'Same result'],
    ['uncertain', 'Not sure'],
    ['improved', 'Better'],
    ['failed', 'Could not replay'],
  ] as const)('labels %s as "%s"', (outcome, label) => {
    render(<OutcomeChip outcome={outcome} />)
    expect(screen.getByRole('img', { name: `Outcome: ${label}` })).toHaveTextContent(label)
  })
})

describe('TierBadge', () => {
  it('shows the tier letter and a keyboard-reachable tooltip with confidence and first reason', async () => {
    render(<TierBadge tier="super" verdict="regressed" confidence={0.86} reasons={['VAT field missing.', 'second']} />)
    const btn = screen.getByRole('button', { name: 'Nemotron Super: regressed, 86%' })
    expect(btn).toHaveTextContent('S')
    btn.focus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Nemotron Super judged regressed with 86% confidence. VAT field missing.')
  })

  it('explains a skipped tier', async () => {
    render(<TierBadge tier="ultra" skipped />)
    screen.getByRole('button', { name: 'Nemotron Ultra: not needed' }).focus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent('Not needed: decided earlier in the cascade.')
  })
})
