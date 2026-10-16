/**
 * Playwright against a running ratchet-control container (nightly.yml), as opposed to the
 * dashboard's own fixture-mode suite (dashboard/playwright.config.ts). The nightly job copies
 * this file to dashboard/ and the specs to dashboard/e2e-control/ so that @playwright/test
 * resolves from dashboard/node_modules; nothing here is imported by the dashboard build.
 *
 *   RATCHET_E2E_URL=http://127.0.0.1:8080 npx playwright test -c playwright.control.config.ts
 */
import { defineConfig, devices } from '@playwright/test'

const BASE_URL = process.env.RATCHET_E2E_URL ?? 'http://127.0.0.1:8080'

export default defineConfig({
  testDir: './e2e-control',
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report-control' }]],
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    colorScheme: 'dark',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } },
    { name: 'phone', use: { ...devices['Pixel 7'] } },
  ],
})
