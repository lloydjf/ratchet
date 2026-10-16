import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { isApiError, type RatchetApi, type StreamHandlers } from '../../api/client'
import { createFixtureApi } from '../../api/fixtures/index'
import { LiveEventsProvider } from '../../api/live'
import { setApi } from '../../api/select'
import type { Event } from '../../api/types'
import { errorCopy } from '../../copy'
import { renderUI } from '../../test/utils'
import VerdictPage from '../VerdictPage'

// History verdicts in the fixture backend: v_rec_gt = BLOCK (reverted), v_7d20e8 = WARN (held),
// v_rec_fx = PASS (merged), v_9a41c2 = PASS that Jun rolled back.
let fixture: RatchetApi

beforeEach(() => {
  sessionStorage.setItem('ratchet.fixture.operator', '1') // operator host: actions are not owner-only
  fixture = createFixtureApi()
  setApi(fixture)
})

function open(id: string, live = false) {
  const routes = (
    <Routes>
      <Route path="/verdicts/:id" element={<VerdictPage />} />
    </Routes>
  )
  renderUI(live ? <LiveEventsProvider>{routes}</LiveEventsProvider> : routes, { route: `/verdicts/${id}` })
  return screen.findByTestId('verdict-header')
}

const actionButtons = (header: HTMLElement) =>
  within(header)
    .queryAllByRole('button')
    .map((b) => b.textContent)

describe('Verdict header actions follow the action taken', () => {
  it('BLOCK (reverted): no Rollback; says main is unchanged and offers Approve anyway', async () => {
    const header = await open('v_rec_gt')
    expect(header).toHaveAttribute('data-decision', 'BLOCK')
    expect(within(header).getByTestId('acted-status')).toHaveTextContent(/^Reverted on candidate · main is unchanged/)
    expect(actionButtons(header)).toEqual(['Approve anyway'])
    expect(within(header).getByText('Undo: Rollback stays available.')).toBeInTheDocument()
  })

  it('Approve anyway confirms, re-applies, toasts, and the header updates from the returned view', async () => {
    const user = userEvent.setup()
    const header = await open('v_rec_gt')
    await user.click(within(header).getByRole('button', { name: 'Approve anyway' }))
    const dialog = screen.getByRole('dialog', { name: 'Approve this blocked edit?' })
    expect(dialog).toHaveTextContent(
      'This re-applies the reverted edit to github-triage and merges it into main, overriding the block. Your agent will use it on its next task. Rollback stays available.',
    )
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus()
    await user.click(within(dialog).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Approved anyway. github-triage is re-applied and live on main.')
    await waitFor(() => expect(within(header).getByTestId('acted-status')).toHaveAttribute('data-action', 'merged'))
    expect(within(header).getByTestId('acted-status')).toHaveTextContent(/Merged to main( · commit [0-9a-f]{7})? · approved anyway at/)
    expect(actionButtons(header)).toEqual(['Rollback'])
  })

  it('WARN (held): Approve and Rollback; Rollback shows who rolled it back', async () => {
    const user = userEvent.setup()
    const header = await open('v_7d20e8')
    expect(actionButtons(header)).toEqual(['Approve', 'Rollback'])
    await user.click(within(header).getByRole('button', { name: 'Rollback' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Roll back this edit?' })).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Rolled back. main is unchanged.')
    expect(await within(header).findByTestId('rolled-back')).toHaveTextContent(/^↺Rolled back by you at \d{2}:\d{2}/)
    // Now reverted: Rollback is gone; Approve anyway is the override that re-applies it.
    expect(actionButtons(header)).toEqual(['Approve anyway'])
  })

  it('PASS (merged): Rollback takes the edit out of main and says so', async () => {
    const user = userEvent.setup()
    const header = await open('v_rec_fx')
    await user.click(within(header).getByRole('button', { name: 'Rollback' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Roll back this edit?' })).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Rolled back. main no longer has this edit.')
    expect(await within(header).findByTestId('rolled-back')).toBeInTheDocument()
    expect(actionButtons(header)).toEqual(['Approve anyway'])
  })

  it('shows the toast on the action response at once, ahead of the live event', async () => {
    setApi({
      ...fixture,
      // The fixture emits verdict.rolled_back (with its own toast) before it answers.
      rollback: async (id) => ({ ...(await fixture.rollback(id)), toast: 'Rolled back. From the response.' }),
    })
    const user = userEvent.setup()
    const header = await open('v_rec_fx', true)
    await user.click(within(header).getByRole('button', { name: 'Rollback' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Roll back this edit?' })).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Rolled back. From the response.')
  })

  it("older backend (no response toast): waits for the live event's toast over the client template", async () => {
    let live: StreamHandlers | null = null
    setApi({
      ...fixture,
      openStream: (since, h) => {
        live = h
        return fixture.openStream(since, h)
      },
      rollback: async (id) => {
        const res = { ...(await fixture.rollback(id)), toast: null }
        const e: Event = { id: 99_999, ts: new Date().toISOString(), kind: 'verdict.rolled_back', ref: id, payload: { verdict_id: id, toast: 'Rolled back. Server wording.' }, session_hash: null }
        live?.onEvent(e)
        return res
      },
    })
    const user = userEvent.setup()
    const header = await open('v_rec_fx', true)
    await waitFor(() => expect(live).not.toBeNull())
    await user.click(within(header).getByRole('button', { name: 'Rollback' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Roll back this edit?' })).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Rolled back. Server wording.')
  })

  it('PASS (merged): Rollback only', async () => {
    const header = await open('v_rec_fx')
    expect(actionButtons(header)).toEqual(['Rollback'])
  })

  it('rolled back: shows actor and time; Approve anyway re-applies it, Rollback is hidden', async () => {
    const user = userEvent.setup()
    const header = await open('v_9a41c2')
    expect(within(header).getByTestId('rolled-back')).toHaveTextContent('Rolled back by jun at')
    expect(within(header).getByRole('link', { name: 're-apply from the edit page' })).toHaveAttribute('href', '/edits/e_9a41c2')
    expect(actionButtons(header)).toEqual(['Approve anyway'])
    await user.click(within(header).getByRole('button', { name: 'Approve anyway' }))
    await user.click(within(screen.getByRole('dialog', { name: 'Approve this blocked edit?' })).getByTestId('confirm-action'))
    expect(await screen.findByTestId('toast')).toHaveTextContent('Approved anyway. changelog-writer is re-applied and live on main.')
    await waitFor(() => expect(within(header).getByTestId('acted-status')).toHaveAttribute('data-action', 'merged'))
    expect(within(header).getByTestId('acted-status')).toHaveTextContent('approved anyway at')
    expect(actionButtons(header)).toEqual(['Rollback'])
  })

  it('reading order is decision → title → actions → metadata', async () => {
    const header = await open('v_7d20e8')
    const chip = within(header).getByRole('img', { name: 'Decision: WARN' })
    const title = within(header).getByRole('heading', { level: 1 })
    const actions = within(header).getByTestId('verdict-actions')
    const meta = header.querySelector('dl')!
    const order = [chip, title, actions, meta]
    for (let i = 1; i < order.length; i++)
      expect(order[i - 1]!.compareDocumentPosition(order[i]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('every dollar total carries its tiers', async () => {
    const header = await open('v_rec_er')
    expect(within(header).getByTestId('usd-tiers')).toHaveTextContent(/^\$0\.\d+ \(nano, super, ultra\)$/)
  })
})

describe('Evidence quality counts control replays (ADR 0023)', () => {
  it('reads "Control replays: N (M diverged)"', async () => {
    await open('v_rec_er')
    expect(await screen.findByTestId('evidence-controls')).toHaveTextContent(/^Control replays: 4 \(1 diverged\)$/)
  })

  it('reads 0 when nothing was contested', async () => {
    await open('v_rec_gt')
    expect(await screen.findByTestId('evidence-controls')).toHaveTextContent(/^Control replays: 0 \(0 diverged\)$/)
  })
})

describe('fixture backend mirrors the real actions', () => {
  it('Rollback on a reverted (BLOCK) verdict is 409 already_reverted', async () => {
    const err = await fixture.rollback('v_rec_gt').catch((e: unknown) => e)
    expect(isApiError(err) && err.code).toBe('already_reverted')
    expect(isApiError(err) && err.status).toBe(409)
    expect(errorCopy('already_reverted')).toBe('This edit is already reverted; main is unchanged.')
  })

  it('Rollback on a rolled-back verdict is 409 already_reverted', async () => {
    const err = await fixture.rollback('v_9a41c2').catch((e: unknown) => e)
    expect(isApiError(err) && err.code).toBe('already_reverted')
  })

  it('events carry the server toast, override and drift fact ids', async () => {
    const seen: Event[] = []
    fixture.openStream(null, { onEvent: (e) => seen.push(e) })
    await fixture.rollback('v_rec_fx')
    await fixture.approve('v_rec_gt')
    await fixture.rebless('run_fx_01', 'rep_v_rec_fx_01')
    const rb = seen.find((e) => e.kind === 'verdict.rolled_back')!
    expect(rb.payload).toMatchObject({ verdict_id: 'v_rec_fx', toast: 'Rolled back. main no longer has this edit.' })
    const acted = seen.find((e) => e.kind === 'verdict.acted')!
    expect(acted.payload).toMatchObject({ verdict_id: 'v_rec_gt', action: 'merged', override: true, toast: 'Approved anyway. github-triage is re-applied and live on main.' })
    const rebless = seen.find((e) => e.kind === 'pair.reblessed')!
    expect(Array.isArray(rebless.payload.drift_fact_ids)).toBe(true)
  })

  it('action responses carry the server toast too (VerdictView.toast, ReblessView.toast)', async () => {
    expect(await fixture.rollback('v_rec_fx')).toMatchObject({ toast: 'Rolled back. main no longer has this edit.' })
    expect(await fixture.approve('v_7d20e8')).toMatchObject({ toast: 'Merged. release-notes is live.' })
    expect(await fixture.rebless('run_fx_01', 'rep_v_rec_fx_01')).toMatchObject({ toast: "Re-blessed. Future checks compare against today's result." })
  })

  it('Rollback / Approve before the edit is terminal answer 409 not_ready', async () => {
    const sim = await fixture.simulate('github-triage')
    const err = await fixture.approve(sim.verdict_id!).catch((e: unknown) => e)
    expect(isApiError(err) && err.code).toBe('not_ready')
    expect(isApiError(err) && err.status).toBe(409)
    expect(errorCopy('not_ready')).toBe('Ratchet is still acting on this verdict. Try again in a few seconds.')
  })

  it('Approve on a reverted verdict re-applies and merges', async () => {
    const res = await fixture.approve('v_rec_gt')
    const v = 'verdict' in res && 'edit' in res ? res.verdict! : res
    expect(v.action).toBe('merged')
    expect(v.approved_at).not.toBeNull()
  })
})
