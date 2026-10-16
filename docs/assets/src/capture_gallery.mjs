// Capture the Devpost gallery images (1280x720) from a running dashboard.
// The dashboard must be serving the live-recorded data (`ratchet serve` with `RATCHET_SIMULATE_MODE=replayed`).
// Page loads only read stored verdicts; nothing here presses Simulate or calls a model.
// Usage: node docs/assets/src/capture_gallery.mjs [baseUrl]
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const require = createRequire(path.join(root, 'dashboard/package.json'))
const { chromium } = require('@playwright/test')
const BASE = process.argv[2] || 'http://127.0.0.1:8090'
const OUT = path.join(root, 'docs/assets')

// Each shot: a CSS viewport whose width picks the layout, scaled by dsf to exactly 1280x720.
const shots = [
  // Two-column landing; the scenario cards in full under the sticky header.
  { name: 'gallery-1-landing', url: '/', vw: 1088, anchor: 'Three skill edits to try', gap: 8 },
  // BLOCK header, narrative and the cost panel side by side (two-column layout starts at 1024).
  { name: 'gallery-2-block-verdict', url: '/verdicts/verdict_fa80c0517746', vw: 1088, anchor: 'BLOCK', anchorKind: 'card', gap: 10 },
  // Single column at 800 so the drift card spans the frame at 1.6x.
  { name: 'gallery-3-drift-card', url: '/verdicts/verdict_76334528a026', vw: 800, anchor: 'World drift', gap: 18 },
  // Single column at 800: the cost panel with the all-Ultra counterfactual at 1.6x.
  { name: 'gallery-4-cost-panel', url: '/verdicts/verdict_8796c4358b15', vw: 800, anchor: 'Cost of this verdict', gap: 0, center: true },
]

const only = process.env.ONLY
const browser = await chromium.launch()
for (const s of shots) {
  if (only && !s.name.includes(only)) continue
  const vh = Math.round((s.vw * 9) / 16)
  const ctx = await browser.newContext({ viewport: { width: s.vw, height: vh }, deviceScaleFactor: 1280 / s.vw, colorScheme: 'dark', reducedMotion: 'reduce' })
  // Keep the demo funnel clean: answer analytics beacons locally.
  await ctx.route('**/api/v1/demo/events', (r) => r.fulfill({ status: 204, body: '' }))
  const page = await ctx.newPage()
  await page.goto(BASE + s.url, { waitUntil: 'networkidle' })
  await page.evaluate(() => document.fonts.ready)
  await page.waitForTimeout(600)
  const y = await page.evaluate(({ anchor, anchorKind, gap, center, vh }) => {
    const header = document.querySelector('header')
    const navH = header ? header.getBoundingClientRect().height : 0
    const els = [...document.querySelectorAll('h1,h2,h3,h4,p,span,div,section')]
    const re = new RegExp(anchor, 'i')
    let el = els.find((e) => e.children.length === 0 && re.test(e.textContent || '') && (e.textContent || '').trim().length < 60)
    if (!el) return null
    if (anchorKind === 'card') {
      // climb from the chip to the first wide bordered box: the decision header card
      let e = el.parentElement
      while (e && !(getComputedStyle(e).borderTopWidth !== '0px' && e.getBoundingClientRect().width > 600)) e = e.parentElement
      el = e || el
    }
    let box = el
    if (center) box = el.closest('section,aside,div[class*="border"]') || el
    const r = box.getBoundingClientRect()
    const top = r.top + window.scrollY
    if (center) return Math.max(0, top - navH - (vh - navH - r.height) / 2)
    return Math.max(0, top - navH - gap)
  }, { ...s, vh })
  if (y === null) throw new Error(`anchor not found: ${s.anchor}`)
  await page.evaluate((y) => window.scrollTo(0, y), y)
  await page.waitForTimeout(300)
  const file = path.join(OUT, `${s.name}.png`)
  await page.screenshot({ path: file })
  console.log('wrote', path.relative(root, file), `viewport ${s.vw}x${vh} @${(1280 / s.vw).toFixed(3)}x, scrollY ${Math.round(y)}`)
  await ctx.close()
}
await browser.close()
