import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

test.describe('mobile 390×844', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2 })

  test('verdict header is readable without scrolling sideways', async ({ page }) => {
    await page.goto('/verdicts/v_rec_gt')
    const header = page.getByTestId('verdict-header')
    await expect(header).toBeVisible()
    const chip = header.getByRole('img', { name: 'Decision: BLOCK' })
    const title = page.getByTestId('verdict-headline')
    await expect(chip).toBeInViewport()
    await expect(title).toBeInViewport()
    const chipBox = (await chip.boundingBox())!
    const titleBox = (await title.boundingBox())!
    expect(chipBox.y + chipBox.height).toBeLessThanOrEqual(titleBox.y + 1) // chip above title
    expect(titleBox.x).toBeGreaterThanOrEqual(16)
    expect(titleBox.x + titleBox.width).toBeLessThanOrEqual(390 - 16)
    const fontSize = await title.evaluate((el) => parseFloat(getComputedStyle(el).fontSize))
    expect(fontSize).toBeGreaterThanOrEqual(20)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
    await expect(page.getByTestId('pair-grid')).toHaveAttribute('data-layout', 'cards')
  })

  test('landing stacks the cards and has no horizontal scroll', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByTestId('scenario-card')).toHaveCount(3)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  })

  test('pair detail uses Golden | Replay tabs with a differences-only toggle', async ({ page }) => {
    await page.goto('/verdicts/v_rec_er/pairs/run_er_01/rep_v_rec_er_01')
    await expect(page.getByRole('tab', { name: 'Replay' })).toHaveAttribute('aria-selected', 'true')
    await page.getByRole('tab', { name: 'Golden' }).click()
    await expect(page.getByTestId('steps-golden')).toBeVisible()
    await page.getByLabel('Differences only').check()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  })
})

for (const scheme of ['dark', 'light'] as const) {
  test.describe(`axe (${scheme})`, () => {
    test.use({ colorScheme: scheme })
    for (const [name, path] of [
      ['landing', '/'],
      ['verdict (BLOCK)', '/verdicts/v_rec_gt'],
      ['verdict (PASS with drift)', '/verdicts/v_rec_fx'],
      ['pair detail', '/verdicts/v_rec_er/pairs/run_er_01/rep_v_rec_er_01'],
      ['timeline', '/timeline'],
      ['costs', '/costs'],
    ] as const) {
      test(`${name} has no serious or critical violations`, async ({ page }) => {
        await page.goto(path)
        await page.waitForLoadState('networkidle')
        const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
        const bad = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
        expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).slice(0, 3).join(' | ')}`)).toEqual([])
      })
    }
  })
}
