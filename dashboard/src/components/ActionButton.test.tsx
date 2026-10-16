import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ApiError } from '../api/client'
import { renderUI } from '../test/utils'
import { NOT_READY_RETRY_MS } from '../copy'
import { ActionButton } from './ActionButton'

describe('ActionButton', () => {
  it('confirms Rollback with the exact confirm body, then runs the action', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    renderUI(<ActionButton kind="rollback" subject="8b41e07" onConfirm={onConfirm} />)
    expect(screen.getByText('Undo: re-apply from the edit page.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Rollback' }))
    const dialog = screen.getByRole('dialog', { name: 'Roll back this edit?' })
    expect(dialog).toHaveTextContent('This reverts commit 8b41e07 on candidate and leaves main unchanged. You can re-apply it later from the edit page.')
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    expect(onConfirm).not.toHaveBeenCalled()
    await user.click(screen.getByTestId('confirm-action'))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('closes on Escape without acting and traps focus inside the dialog', async () => {
    const user = userEvent.setup()
    const onConfirm = vi.fn()
    renderUI(<ActionButton kind="approve" subject="release-notes" onConfirm={onConfirm} />)
    await user.click(screen.getByRole('button', { name: 'Approve' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('This merges candidate into main. Your agent will use the new release-notes on its next task. Rollback stays available.')
    await user.tab()
    expect(screen.getByTestId('confirm-action')).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(onConfirm).not.toHaveBeenCalled()
  })

  it('shows code-mapped copy when the action fails and keeps the dialog open', async () => {
    const user = userEvent.setup()
    renderUI(<ActionButton kind="rebless" subject="Berlin trip to USD" onConfirm={() => Promise.reject(new ApiError('action_locked', 'locked', 409))} />)
    await user.click(screen.getByRole('button', { name: 'Re-bless' }))
    expect(screen.getByRole('dialog')).toHaveTextContent('This makes the replay the new reference for "Berlin trip to USD" and archives the old one with the reason "world changed".')
    await user.click(screen.getByTestId('confirm-action'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Someone else is acting on this verdict.')
  })

  it('409 not_ready: shows its copy, then re-enables the action after a few seconds', async () => {
    vi.useFakeTimers()
    try {
      const onConfirm = vi.fn().mockRejectedValueOnce(new ApiError('not_ready', "this edit's revert has not run yet", 409)).mockResolvedValueOnce(undefined)
      renderUI(<ActionButton kind="approveAnyway" subject="github-triage" onConfirm={onConfirm} />)
      fireEvent.click(screen.getByRole('button', { name: 'Approve anyway' }))
      await act(async () => {
        fireEvent.click(screen.getByTestId('confirm-action'))
      })
      expect(screen.getByRole('alert')).toHaveTextContent('Ratchet is still acting on this verdict. Try again in a few seconds.')
      expect(screen.getByTestId('confirm-action')).toBeDisabled()
      await act(async () => {
        vi.advanceTimersByTime(NOT_READY_RETRY_MS + 10)
      })
      expect(screen.getByTestId('confirm-action')).toBeEnabled()
      await act(async () => {
        fireEvent.click(screen.getByTestId('confirm-action'))
      })
      expect(onConfirm).toHaveBeenCalledTimes(2)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })

  it("429: shows the server's sentence (per-client simulate busy, rate limit)", async () => {
    const user = userEvent.setup()
    const msg = 'Too many actions in a minute. Wait a moment and try again.'
    renderUI(<ActionButton kind="rollback" subject="8b41e07" onConfirm={() => Promise.reject(new ApiError('rate_limited', msg, 429))} />)
    await user.click(screen.getByRole('button', { name: 'Rollback' }))
    await user.click(screen.getByTestId('confirm-action'))
    expect(await screen.findByRole('alert')).toHaveTextContent(msg)
  })
})
