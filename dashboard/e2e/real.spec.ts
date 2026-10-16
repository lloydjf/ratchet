import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

/**
 * The judge path against a real `ratchet serve` (sim upstream). Run with
 * `E2E_BASE_URL=http://127.0.0.1:8080 npm run e2e`. Scenario ids and copy come from the
 * server, so assertions are structural plus the fixed decision words.
 */
async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  return r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)
}

test('real backend: landing → Simulate 1 → verdict → pair → back → action for the state', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  await expect(page.getByTestId('scenario-card')).toHaveCount(3)
  expect(await axe(page)).toEqual([])
  const card = page.getByTestId('scenario-card').nth(0)
  await card.getByTestId('simulate').click()
  await expect(card.getByTestId('progress-strip').or(card.getByRole('status'))).toBeVisible({ timeout: 30_000 })
  await expect(card.getByRole('img', { name: /^Decision: (BLOCK|WARN|PASS)$/ })).toBeVisible({ timeout: 150_000 })
  await card.getByTestId('open-verdict').click()
  await expect(page.getByTestId('verdict-headline')).toHaveText(/^(Blocked|Held|Passed):/)
  await page.screenshot({ path: 'screenshots/real-verdict-1-dark-desktop.png', fullPage: true })
  await expect(page).toHaveTitle(/^(BLOCK|WARN|PASS) — .+ — Ratchet$/)
  expect(await axe(page)).toEqual([])
  const open = page.getByTestId('pair-grid').getByRole('link', { name: /^Open / })
  if (await open.count()) {
    await open.first().click()
    await expect(page.getByTestId('pair-header')).toBeVisible()
    await page.goBack()
  } else {
    // Every replay failed (replay_infra): rows say so instead of linking nowhere.
    await expect(page.getByTestId('pair-grid')).toContainText('No replay')
  }
  const header = page.getByTestId('verdict-header')
  await expect(header).toBeVisible()
  await expectActionsForState(page)
  if ((await header.getAttribute('data-action')) === 'reverted') {
    // BLOCK: approve anyway re-applies the edit; the header then shows it merged with Rollback.
    await header.getByRole('button', { name: 'Approve anyway' }).click()
    await page.getByRole('dialog', { name: 'Approve this blocked edit?' }).getByTestId('confirm-action').click()
    await expect(page.getByTestId('toast')).toContainText(/Approved anyway\. .+ is re-applied and live on main\./)
    await expect(header).toHaveAttribute('data-action', 'merged')
    await expect(header.getByRole('button', { name: 'Rollback' })).toBeVisible()
  }
  expect(errors).toEqual([])
})

/** Verdict actions: merged → Rollback; held → Approve + Rollback; reverted (BLOCK) → Approve anyway only. */
async function expectActionsForState(page: Page) {
  const header = page.getByTestId('verdict-header')
  const action = await header.getAttribute('data-action')
  const buttons = header.getByTestId('verdict-actions').getByRole('button')
  if (action === 'reverted') {
    await expect(header.getByTestId('acted-status')).toContainText('Reverted on candidate · main is unchanged')
    await expect(buttons).toHaveText(['Approve anyway'])
  } else if (action === 'held') await expect(buttons).toHaveText(['Approve', 'Rollback'])
  else await expect(buttons).toHaveText(['Rollback'])
}

test('real backend: scenario 3 drift card, then Rollback on a merged or held verdict', async ({ page }) => {
  await page.goto('/')
  const card = page.getByTestId('scenario-card').nth(2)
  await card.getByTestId('simulate').click()
  await expect(card.getByRole('img', { name: /^Decision: (BLOCK|WARN|PASS)$/ })).toBeVisible({ timeout: 150_000 })
  await card.getByTestId('open-verdict').click()
  await expect(page.getByTestId('verdict-headline')).toHaveText(/^(Blocked|Held|Passed):/)
  // Drift cards need successful replays; with replay_infra the page still renders cleanly.
  const failedAll = await page.getByTestId('pair-grid').getByText('No replay').count()
  if (!failedAll) await expect(page.getByTestId('drift-card').first()).toBeVisible()
  await page.screenshot({ path: 'screenshots/real-verdict-3-dark-desktop.png', fullPage: true })
  const header = page.getByTestId('verdict-header')
  await expectActionsForState(page)
  if ((await header.getAttribute('data-action')) !== 'reverted') {
    await header.getByRole('button', { name: 'Rollback' }).click()
    await page.getByRole('dialog', { name: 'Roll back this edit?' }).getByTestId('confirm-action').click()
    await expect(page.getByTestId('toast')).toContainText(/Rolled back\. main (is unchanged|no longer has this edit)\./)
    await expect(header.getByTestId('rolled-back')).toContainText(/^↺Rolled back by .+ at /)
    await expect(header.getByTestId('verdict-actions').getByRole('button')).toHaveText(['Approve anyway'])
  }
})

test('real backend: timeline, costs, digest render without errors', async ({ page }) => {
  for (const path of ['/timeline', '/verdicts', '/costs', '/digest']) {
    await page.goto(path)
    await expect(page.locator('main')).not.toBeEmpty()
    await expect(page.getByTestId('error-state')).toHaveCount(0)
  }
})
