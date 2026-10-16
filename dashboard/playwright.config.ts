import { defineConfig, devices } from '@playwright/test'

const PORT = 4174
/** Set E2E_BASE_URL (e.g. http://127.0.0.1:8080 from `ratchet serve`) to test the real backend. */
const REAL = process.env.E2E_BASE_URL

/** E2E runs against a fixture-mode production build served by `vite preview`, or a real host. */
export default defineConfig({
  testDir: './e2e',
  testMatch: REAL ? ['real.spec.ts'] : ['*.spec.ts'],
  testIgnore: REAL ? [] : ['real.spec.ts'],
  timeout: REAL ? 180_000 : 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: REAL ?? `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    colorScheme: 'dark',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: REAL
    ? undefined
    : {
        command: `npm run build:fixtures && npx vite preview --outDir dist-fixtures --port ${PORT} --strictPort --host 127.0.0.1`,
        url: `http://127.0.0.1:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
})
