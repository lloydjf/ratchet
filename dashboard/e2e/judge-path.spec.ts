import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// Fixture runs take ~20 s at speed 1; e2e runs them at 2× (still every stage on screen).
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as unknown as { __RATCHET_FIXTURE_SPEED__: number }).__RATCHET_FIXTURE_SPEED__ = 2
  })
})

test('cold open → Simulate 1 → BLOCK verdict → pair → back → Approve anyway', async ({ page }) => {
  const errors: string[] = []
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text())
  })
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  await expect(page).toHaveTitle('Ratchet')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your agent learns new skills every day. Ratchet makes sure it never unlearns the old ones.')
  const card = page.locator('[data-scenario="github-triage"]')
  await expect(card.getByRole('img', { name: 'Expected decision: BLOCK' })).toBeVisible()

  await card.getByTestId('simulate').click()
  await expect(card).toHaveAttribute('data-status', 'running')
  const strip = card.getByTestId('progress-strip')
  await expect(strip).toBeVisible()
  // A stage label is always on screen while running.
  await expect(strip.locator('[aria-live="polite"]')).not.toBeEmpty()

  await expect(card.getByRole('img', { name: 'Decision: BLOCK' })).toBeVisible({ timeout: 40_000 })
  await card.getByTestId('open-verdict').click()

  await expect(page).toHaveURL(/\/verdicts\/v_sim/)
  await expect(page).toHaveTitle('BLOCK — github-triage — Ratchet')
  const header = page.getByTestId('verdict-header')
  await expect(header.getByRole('img', { name: 'Decision: BLOCK' })).toBeVisible()
  await expect(page.getByTestId('verdict-headline')).toHaveText('Blocked: the new github-triage would close issues in 6 of 14 replays.')
  await expect(page.getByTestId('cost-saved')).toContainText('cascade saved')

  // Evidence: the first row is a regression with the Level 0 copy; open it.
  const firstRow = page.getByTestId('pair-grid').locator('[data-outcome]').first()
  await expect(firstRow).toHaveAttribute('data-outcome', 'regression')
  await expect(firstRow).toContainText('Checked without a model:')
  await page.evaluate(() => window.scrollTo(0, 900))
  const scrollBefore = await page.evaluate(() => window.scrollY)
  await firstRow.getByRole('link', { name: /^Open / }).click()

  await expect(page).toHaveURL(/\/pairs\//)
  await expect(page.getByTestId('pair-header').getByRole('img', { name: 'Outcome: Got worse' })).toBeVisible()
  await expect(page.getByTestId('level0-card')).toContainText('github.close_issue')
  // ADR 0024: the final-output line; Level 0 decided this pair, so no Nano note.
  await expect(page.getByTestId('level0-output')).toHaveText(/^Final output: (identical|\d+ % similar)$/)
  await expect(page.getByTestId('level0-nano-gated')).toHaveCount(0)
  await page.getByTestId('jump-to-diff').click()
  await expect(page.locator('[data-testid="steps-replay"] [data-differs]').first()).toBeVisible()

  await page.goBack()
  await expect(page.getByTestId('verdict-header')).toBeVisible()
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(scrollBefore - 50)

  // A BLOCK is already reverted on candidate: nothing to roll back, but it can be approved anyway.
  await expect(header).toHaveAttribute('data-action', 'reverted')
  await expect(header.getByTestId('acted-status')).toContainText('Reverted on candidate · main is unchanged')
  await expect(header.getByRole('button', { name: 'Rollback' })).toHaveCount(0)
  await header.getByRole('button', { name: 'Approve anyway' }).click()
  const dialog = page.getByRole('dialog', { name: 'Approve this blocked edit?' })
  await expect(dialog).toContainText(
    'This re-applies the reverted edit to github-triage and merges it into main, overriding the block. Your agent will use it on its next task. Rollback stays available.',
  )
  await dialog.getByTestId('confirm-action').click()
  await expect(page.getByTestId('toast')).toContainText('Approved anyway. github-triage is re-applied and live on main.')
  await expect(header).toHaveAttribute('data-action', 'merged')
  await expect(header.getByTestId('acted-status')).toContainText('Merged to main')
  await expect(header.getByTestId('acted-status')).toContainText('approved anyway at')
  await expect(header.getByTestId('verdict-actions').getByRole('button')).toHaveText(['Rollback'])
  expect(errors).toEqual([])
})

test('Simulate 2 → the controls stage replays contested pairs; one is held as nondeterminism (ADR 0023)', async ({ page }) => {
  // Speed 1 keeps the ~2.5 s controls stage on screen long enough to assert on.
  await page.addInitScript(() => {
    ;(window as unknown as { __RATCHET_FIXTURE_SPEED__: number }).__RATCHET_FIXTURE_SPEED__ = 1
  })
  await page.goto('/')
  const card = page.locator('[data-scenario="expense-report"]')
  await card.getByTestId('simulate').click()
  const strip = card.getByTestId('progress-strip')
  await expect(strip).toBeVisible()
  // The controls segment sits after Tavily (drift) from the start.
  const segs = strip.getByRole('list', { name: 'Stages' }).getByRole('listitem')
  await expect(segs).toHaveCount(10)
  await expect(segs.nth(8)).toHaveAttribute('aria-label', /^Replaying controls( \(\d\/4\))?: /)
  // While controlling, the label counts control batches (4 contested pairs), not the 10 replays.
  await expect(strip).toHaveAttribute('data-stage', 'controls', { timeout: 40_000 })
  await expect(strip.locator('[aria-live="polite"]')).toContainText(/Replaying controls \([0-4]\/4\)/)
  await expect(strip.locator('[aria-live="polite"]')).not.toContainText('/10')

  await expect(card.getByRole('img', { name: 'Decision: BLOCK' })).toBeVisible({ timeout: 40_000 })
  await card.getByTestId('open-verdict').click()
  await expect(page).toHaveURL(/\/verdicts\/v_sim/)
  await expect(page.getByTestId('evidence-controls')).toHaveText('Control replays: 4 (1 diverged)')
  const grid = page.getByTestId('pair-grid')
  await expect(grid.getByText('Control: diverges')).toHaveCount(1)
  await expect(grid.getByText('Control: agrees')).toHaveCount(3)
  await expect(grid.getByTestId('trail-control')).toHaveCount(1)
  // The control-decided pair reads "Same result (noisy task)".
  const noisy = grid.locator('[data-outcome]', { has: page.getByTestId('noisy-task') })
  await expect(noisy).toHaveAttribute('data-outcome', 'pass')
  await expect(noisy.getByTestId('noisy-task')).toHaveText('(noisy task)')
})

test('held (WARN) verdict offers Approve and Rollback; Rollback updates the header', async ({ page }) => {
  await page.goto('/?operator=1')
  await page.goto('/verdicts/v_7d20e8')
  const header = page.getByTestId('verdict-header')
  await expect(header).toHaveAttribute('data-action', 'held')
  await expect(header.getByTestId('verdict-actions').getByRole('button')).toHaveText(['Approve', 'Rollback'])
  await header.getByRole('button', { name: 'Rollback' }).click()
  const dialog = page.getByRole('dialog', { name: 'Roll back this edit?' })
  await expect(dialog).toContainText('on candidate and leaves main unchanged. You can re-apply it later from the edit page.')
  await dialog.getByTestId('confirm-action').click()
  await expect(page.getByTestId('toast')).toContainText('Rolled back. main is unchanged.')
  await expect(header.getByTestId('rolled-back')).toContainText('Rolled back by you at')
  // Reverted now: Rollback is hidden; Approve anyway re-applies it.
  await expect(header.getByTestId('verdict-actions').getByRole('button')).toHaveText(['Approve anyway'])
})

test('Rollback on a merged edit says main no longer has it', async ({ page }) => {
  await page.goto('/?operator=1')
  await page.goto('/verdicts/v_rec_fx')
  const header = page.getByTestId('verdict-header')
  await expect(header).toHaveAttribute('data-action', 'merged')
  await header.getByRole('button', { name: 'Rollback' }).click()
  await page.getByRole('dialog', { name: 'Roll back this edit?' }).getByTestId('confirm-action').click()
  await expect(page.getByTestId('toast')).toContainText('Rolled back. main no longer has this edit.')
  await expect(header.getByTestId('verdict-actions').getByRole('button')).toHaveText(['Approve anyway'])
  // The debug drawer shows the server's worker log on the operator host.
  await page.getByRole('button', { name: 'Show logs' }).click()
  await expect(page.getByTestId('worker-log')).toContainText('--- worker log')
})

test('operator: recent runs → Bless with a confirm → the run leaves the list', async ({ page }) => {
  await page.goto('/?operator=1')
  await page.goto('/traces?kind=live')
  await expect(page.getByRole('heading', { level: 1, name: 'Recent runs' })).toBeVisible()
  const rows = page.getByTestId('run-row')
  await expect(rows.first()).toBeVisible()
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => v.id)).toEqual([])
  const runId = await rows.first().getAttribute('data-run-id')
  await rows.first().getByRole('button', { name: 'Bless' }).click()
  await page.getByRole('dialog', { name: 'Bless this run?' }).getByTestId('confirm-action').click()
  await expect(page.getByTestId('toast')).toContainText('Blessed. Future edits to its skills are now checked against this run.')
  await expect(page.locator(`[data-testid="run-row"][data-run-id="${runId}"]`)).toHaveCount(0)
})

test('costs: date-range picker defaults to 14 days and shows the window', async ({ page }) => {
  await page.goto('/costs')
  await expect(page.getByTestId('window-tile')).toContainText('Last 14 days')
  await expect(page.getByTestId('window-tile').getByTestId('cache-share')).toHaveText(/^Prompt-cache share: (0\.\d\d of prompt tokens \(nano .+ · super .+ · ultra .+\)|not recorded)$/)
  await page.getByRole('button', { name: '7 days' }).click()
  await expect(page.getByTestId('window-tile')).toContainText('Last 7 days')
  await expect(page.getByRole('heading', { name: 'By purpose: last 7 days' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'This edit' })).toBeEnabled()
  await page.getByRole('button', { name: 'This edit' }).click()
  await expect(page.getByTestId('window-tile')).toContainText('This edit')
})

test('SSE stream cap (429): polls instead and says so', async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as unknown as { __RATCHET_FIXTURE_SSE_LIMITED__: boolean }).__RATCHET_FIXTURE_SSE_LIMITED__ = true
  })
  await page.goto('/timeline')
  await expect(page.getByTestId('live-limited')).toHaveText('Live updates paused (too many open tabs)')
  await expect(page.getByTestId('live-status')).toHaveAttribute('data-status', 'polling')
})

test('public host: /__kit is the landing page and the debug drawer gives the CLI hint', async ({ page }) => {
  await page.goto('/__kit')
  await expect(page.getByRole('heading', { level: 1 })).toHaveText('Your agent learns new skills every day. Ratchet makes sure it never unlearns the old ones.')
  await expect(page.locator('[data-kit]')).toHaveCount(0)
  await expect(page.getByRole('list', { name: 'How a check runs' })).toContainText('Replay on Nebius Jobs')
  await page.goto('/verdicts/v_rec_gt')
  await page.getByRole('button', { name: 'Show logs' }).click()
  await expect(page.getByTestId('logs-hint')).toContainText('ratchet logs --edit e_rec_gt')
})

test('pair page: Level 0 says when Nano could not clear a pair alone (ADR 0024)', async ({ page }) => {
  await page.goto('/verdicts/v_rec_er/pairs/run_er_01/rep_v_rec_er_01')
  const card = page.getByTestId('level0-card')
  await expect(card.getByTestId('level0-output')).toHaveText(/^Final output: \d+ % similar$/)
  await expect(card.getByTestId('level0-nano-gated')).toHaveText('Nano could not clear this pair alone (Level 0 found differences).')
  await page.goto('/verdicts/v_rec_gt/pairs/run_gt_04/rep_v_rec_gt_04')
  await expect(page.getByTestId('level0-output')).toHaveText('Final output: identical')
  await expect(page.getByTestId('level0-nano-gated')).toHaveCount(0)
})

test('scenario 3 shows a drift card and offers Re-bless', async ({ page }) => {
  await page.goto('/')
  const card = page.locator('[data-scenario="fx-expense-converter"]')
  await card.getByTestId('simulate').click()
  await expect(card.getByRole('img', { name: 'Decision: PASS' })).toBeVisible({ timeout: 40_000 })
  await card.getByTestId('open-verdict').click()
  await expect(page.getByTestId('verdict-headline')).toHaveText('Passed: 7 tasks checked; 4 changed because the world did, not the skill.')
  // PASS merged: Rollback is the only header action.
  await expect(page.getByTestId('verdict-header')).toHaveAttribute('data-action', 'merged')
  await expect(page.getByTestId('verdict-actions').getByRole('button')).toHaveText(['Rollback'])
  const drift = page.getByTestId('drift-card').first()
  await expect(drift).toHaveAttribute('data-state', 'changed')
  await expect(drift).toContainText('1.0800')
  await expect(drift).toContainText('1.1630')
  await expect(drift.getByRole('link', { name: /Source/ })).toHaveAttribute('href', /^https:\/\//)
  await page.getByRole('button', { name: 'Re-bless' }).first().click()
  await page.getByRole('dialog').getByTestId('confirm-action').click()
  await expect(page.getByTestId('toast')).toContainText("Re-blessed. Future checks compare against today's result.")
})

test('timeline filters survive back navigation and live toggle pauses', async ({ page }) => {
  await page.goto('/timeline')
  await page.getByRole('radio', { name: 'Verdicts' }).click()
  await expect(page).toHaveURL(/kind=verdicts/)
  const items = page.getByTestId('timeline-item')
  await expect(items.first()).toHaveAttribute('data-kind', 'verdict.decided')
  await items.first().click()
  await expect(page).toHaveURL(/\/verdicts\//)
  await page.goBack()
  await expect(page.getByRole('radio', { name: 'Verdicts' })).toHaveAttribute('aria-checked', 'true')
  await page.getByTestId('live-toggle').click()
  await expect(page.getByTestId('live-toggle')).toHaveText(/Paused/)
})

test('kit renders every component group', async ({ page }) => {
  await page.goto('/__kit?operator=1')
  for (const id of ['decision', 'outcome', 'tier', 'trail', 'ticker', 'costpanel', 'progress', 'diff', 'pairgrid', 'opinion', 'drift', 'steps', 'timeline', 'scenario', 'actions', 'badge', 'empty', 'toast']) {
    await expect(page.locator(`[data-kit="${id}"]`)).toBeVisible()
  }
  await expect(page.locator('[data-testid="progress-strip"][data-status="stalled"]')).toHaveCount(1)
})
