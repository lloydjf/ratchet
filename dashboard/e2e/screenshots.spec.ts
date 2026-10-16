import { test, type Page } from '@playwright/test'

/**
 * Rehearsal screenshots (the /__kit component sheet + key screens), dark and light, desktop and
 * mobile, into dashboard/screenshots/. Run with `npm run screenshots`.
 */
test.skip(!process.env.SCREENSHOTS, 'set SCREENSHOTS=1 to capture screenshots')

const PAGES: [string, string][] = [
  ['landing', '/'],
  ['verdict-1-triage', '/verdicts/v_rec_gt'],
  ['verdict-2-expense', '/verdicts/v_rec_er'],
  ['verdict-3-fx', '/verdicts/v_rec_fx'],
  ['verdict-warn', '/verdicts/v_7d20e8'],
  ['verdict-rolled-back', '/verdicts/v_9a41c2'],
  ['pair-triage', '/verdicts/v_rec_gt/pairs/run_gt_01/rep_v_rec_gt_01'],
  ['pair-expense', '/verdicts/v_rec_er/pairs/run_er_01/rep_v_rec_er_01'],
  ['pair-fx', '/verdicts/v_rec_fx/pairs/run_fx_01/rep_v_rec_fx_01'],
  ['timeline', '/timeline'],
  ['verdicts', '/verdicts'],
  ['costs', '/costs'],
  ['digest', '/digest'],
  ['edit', '/edits/e_rec_gt'],
  ['kit', '/__kit?operator=1'],
]

const ONLY = process.env.SHOTS?.split(',')

async function settle(page: Page) {
  await page.waitForLoadState('networkidle')
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(400)
}

for (const theme of ['dark', 'light'] as const) {
  for (const device of ['desktop', 'mobile'] as const) {
    test.describe(`${theme} ${device}`, () => {
      test.use({
        colorScheme: theme,
        viewport: device === 'mobile' ? { width: 390, height: 844 } : { width: 1440, height: 900 },
        deviceScaleFactor: device === 'mobile' ? 2 : 1,
        isMobile: device === 'mobile',
        hasTouch: device === 'mobile',
      })
      for (const [name, path] of PAGES) {
        if (ONLY && !ONLY.includes(name)) continue
        test(`${name}`, async ({ page }) => {
          await page.goto(path)
          await settle(page)
          await page.evaluate(() => window.scrollTo(0, 0))
          await page.screenshot({ path: `screenshots/${name}-${theme}-${device}.png`, fullPage: true })
          if (device === 'mobile' && ['landing', 'verdict-1-triage', 'verdict-3-fx', 'pair-expense'].includes(name))
            await page.screenshot({ path: `screenshots/${name}-${theme}-${device}-fold.png`, fullPage: false })
        })
      }
    })
  }
}

test.describe('kit groups', () => {
  test.use({ colorScheme: 'dark', viewport: { width: 1280, height: 900 } })
  test('kit groups close up', async ({ page }) => {
    await page.goto('/__kit?operator=1')
    await settle(page)
    for (const id of ['sieve', 'band', 'trail', 'actions', 'decision', 'tier', 'progress', 'scenario', 'drift', 'opinion', 'steps']) {
      await page.locator(`[data-kit="${id}"]`).screenshot({ path: `screenshots/kit-${id}-dark-desktop.png` })
    }
  })
  test('level 0 cards close up', async ({ page }) => {
    for (const [name, path] of [
      ['triage', '/verdicts/v_rec_gt/pairs/run_gt_01/rep_v_rec_gt_01'],
      ['expense', '/verdicts/v_rec_er/pairs/run_er_01/rep_v_rec_er_01'],
    ]) {
      await page.goto(path!)
      await settle(page)
      await page.getByTestId('level0-card').screenshot({ path: `screenshots/level0-${name}-dark-desktop.png` })
    }
  })
})

test.describe('running states', () => {
  test.use({ colorScheme: 'dark', viewport: { width: 1440, height: 900 } })
  test('landing while simulating', async ({ page }) => {
    await page.goto('/')
    await settle(page)
    await page.locator('[data-scenario="github-triage"] [data-testid="simulate"]').click()
    await page.waitForTimeout(6000)
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.waitForTimeout(200)
    await page.screenshot({ path: 'screenshots/landing-running-dark-desktop.png', fullPage: true })
    await page.locator('[data-scenario="github-triage"] a', { hasText: 'Watch it' }).click()
    await page.waitForTimeout(1500)
    await page.screenshot({ path: 'screenshots/verdict-running-dark-desktop.png', fullPage: false })
  })
})

test.describe('verdict actions', () => {
  test.use({ colorScheme: 'dark', viewport: { width: 1440, height: 900 } })
  test('BLOCK header and the Approve anyway inline confirm', async ({ page }) => {
    await page.goto('/?operator=1')
    await page.goto('/verdicts/v_rec_gt')
    await settle(page)
    await page.getByTestId('verdict-header').screenshot({ path: 'screenshots/verdict-block-header-dark-desktop.png' })
    await page.getByRole('button', { name: 'Approve anyway' }).click()
    await page.getByTestId('inline-confirm').waitFor()
    await page.waitForTimeout(300)
    await page.screenshot({ path: 'screenshots/verdict-approve-anyway-confirm-dark-desktop.png', fullPage: false })
  })
})
