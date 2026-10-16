/**
 * Smoke of the built control image in sim mode (nightly.yml): the process is up, the API and
 * the SPA are served by the same origin, deep links fall back to index.html, and the landing
 * renders without uncaught errors. The full judge path (Simulate 1-3) is covered by
 * `ratchet simulate k --headless` in the same job and by the dashboard's own e2e suite.
 */
import { expect, test } from '@playwright/test'

test('healthz reports ok', async ({ request }) => {
  const res = await request.get('/healthz')
  expect(res.status()).toBe(200)
  const body = await res.json()
  expect(body.ok).toBe(true)
})

test('meta is served under /api/v1 and the /api alias', async ({ request }) => {
  for (const path of ['/api/v1/meta', '/api/meta']) {
    const res = await request.get(path)
    expect(res.status(), path).toBe(200)
    expect(res.headers()['content-type'] ?? '').toContain('application/json')
  }
})

test('metrics are Prometheus text and carry no key material', async ({ request }) => {
  const res = await request.get('/metrics')
  expect(res.status()).toBe(200)
  const text = await res.text()
  expect(text).toMatch(/^# (HELP|TYPE) /m)
  expect(text).not.toMatch(/\b(sk|tvly)-[A-Za-z0-9_-]{16,}/)
})

test('landing renders without uncaught errors', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (err) => errors.push(String(err)))
  const res = await page.goto('/')
  expect(res?.status()).toBe(200)
  await expect(page.locator('#root')).not.toBeEmpty()
  await expect(page.locator('h1, h2').first()).toBeVisible()
  expect(errors).toEqual([])
})

test('deep links fall back to the SPA', async ({ page }) => {
  const res = await page.goto('/timeline')
  expect(res?.status()).toBe(200)
  await expect(page.locator('#root')).not.toBeEmpty()
})
