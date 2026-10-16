import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { STAGE_KEYS } from '../copy'
import { controlCounts, ProgressStrip } from './ProgressStrip'

const base = { stage: 'replaying', stageLabel: 'Replaying on Nebius Jobs (9/14)', k: 9, n: 14, elapsedS: 12, usdByTier: { nano: 0.001 } }

describe('ProgressStrip', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('announces the stage politely', () => {
    render(<ProgressStrip {...base} />)
    const live = screen.getByText('Replaying on Nebius Jobs (9/14)').closest('[aria-live]')
    expect(live).toHaveAttribute('aria-live', 'polite')
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'running')
  })

  it('turns stalled after 20 s without a stage change and says so', () => {
    render(<ProgressStrip {...base} />)
    act(() => vi.advanceTimersByTime(19_000))
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'running')
    act(() => vi.advanceTimersByTime(1_500))
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'stalled')
    expect(screen.getByTestId('stalled-copy')).toHaveTextContent('Still working — Replaying on Nebius Jobs (9/14) is taking longer than usual.')
  })

  it('resets the stall clock when the stage changes', () => {
    const { rerender } = render(<ProgressStrip {...base} />)
    act(() => vi.advanceTimersByTime(15_000))
    rerender(<ProgressStrip {...base} stageLabel="Replaying on Nebius Jobs (10/14)" k={10} />)
    act(() => vi.advanceTimersByTime(15_000))
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'running')
  })

  it('honors the server stalled flag and shows done / failed', () => {
    const { rerender } = render(<ProgressStrip {...base} stalled />)
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'stalled')
    rerender(<ProgressStrip {...base} status="done" />)
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'done')
    rerender(<ProgressStrip {...base} status="failed" failedMessage="Replays failed." />)
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'failed')
    expect(screen.getAllByText('Replays failed.').length).toBeGreaterThan(0)
  })

  it('has a controls segment after drift when the server sends no stages', () => {
    render(<ProgressStrip {...base} />)
    const segs = screen.getAllByRole('listitem').map((li) => li.getAttribute('aria-label'))
    expect(segs).toHaveLength(STAGE_KEYS.length - 1)
    expect(segs.indexOf('Replaying controls: pending')).toBe(STAGE_KEYS.indexOf('controls'))
  })

  it('counts control batches for the controls stage, never the replay k/n (ADR 0023)', () => {
    // Progress.k/n stay the edit's replays (14/14); the controls label carries the ctrl_ batch counts.
    const stages = STAGE_KEYS.map((key) => ({ key, label: key === 'controls' ? 'Replaying controls (1/4)' : key, status: key === 'controls' ? ('active' as const) : ('done' as const), detail: '' }))
    const { rerender } = render(<ProgressStrip {...base} stage="controls" stageLabel="Replaying controls (1/4)" k={14} n={14} stages={stages} compact />)
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-stage', 'controls')
    expect(screen.getByText('Replaying controls (1/4)')).toBeInTheDocument()
    expect(screen.getByText('Controls 1/4')).toBeInTheDocument()
    expect(screen.queryByText(/14\/14/)).not.toBeInTheDocument()
    // Without a server label the strip still reads the controls stage entry, not k/n.
    rerender(<ProgressStrip {...base} stageLabel={undefined} stage="controls" k={14} n={14} stages={stages} compact />)
    expect(screen.getByText('Replaying controls (1/4)')).toBeInTheDocument()
    rerender(<ProgressStrip {...base} stageLabel={undefined} stage="controls" k={14} n={14} compact />)
    expect(screen.getByText('Replaying controls')).toBeInTheDocument()
    expect(screen.getByText('Controls')).toBeInTheDocument()
    expect(controlCounts('Replaying controls (3/5)')).toEqual([3, 5])
    expect(controlCounts(null, [])).toEqual([null, null])
  })

  it('keeps the stall behavior on the controls stage', () => {
    render(<ProgressStrip {...base} stage="controls" stageLabel="Replaying controls (1/4)" k={14} n={14} />)
    act(() => vi.advanceTimersByTime(20_500))
    expect(screen.getByTestId('progress-strip')).toHaveAttribute('data-status', 'stalled')
    expect(screen.getByTestId('stalled-copy')).toHaveTextContent('Still working — Replaying controls (1/4) is taking longer than usual.')
  })

  it('shows the running cost with the tiers that incurred it', () => {
    const { rerender } = render(<ProgressStrip {...base} />)
    expect(screen.getByTestId('strip-cost')).toHaveTextContent('$0.0010 (nano)')
    rerender(<ProgressStrip {...base} usdByTier={{}} />)
    expect(screen.getByTestId('strip-cost')).toHaveTextContent('$0.00 (no model yet)')
  })
})
