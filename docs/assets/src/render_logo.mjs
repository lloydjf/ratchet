// Render the logo mark and favicon to PNG with Playwright (transparent background).
// logo-*.png are #E6EDF3 (for dark backgrounds); logo-*-ink.png are #0E1116 (for light ones).
// Run from anywhere: node docs/assets/src/render_logo.mjs
import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const require = createRequire(path.join(root, 'dashboard/package.json'))
const { chromium } = require('@playwright/test')

const logo = readFileSync(path.join(root, 'docs/assets/logo.svg'), 'utf8')
const fav = readFileSync(path.join(root, 'dashboard/public/favicon.svg'), 'utf8')
const color = process.env.LOGO_COLOR || '#E6EDF3'

const jobs = [
  { svg: logo, size: 512, out: 'docs/assets/logo-512.png', color },
  { svg: logo, size: 128, out: 'docs/assets/logo-128.png', color },
  // dark ink variants for light backgrounds
  { svg: logo, size: 512, out: 'docs/assets/logo-512-ink.png', color: '#0E1116' },
  { svg: logo, size: 128, out: 'docs/assets/logo-128-ink.png', color: '#0E1116' },
  { svg: fav, size: 32, out: 'dashboard/public/favicon.png' },
  { svg: fav, size: 180, out: 'dashboard/public/apple-touch-icon.png' },
]
if (process.env.PREVIEW) jobs.push({ svg: logo, size: 512, out: process.env.PREVIEW, color, bg: '#0E1116' })

const browser = await chromium.launch()
for (const j of jobs) {
  const page = await browser.newPage({ viewport: { width: j.size, height: j.size }, deviceScaleFactor: 1 })
  const html = `<!doctype html><html><head><style>html,body{margin:0;background:${j.bg || 'transparent'}}
    svg{display:block;width:${j.size}px;height:${j.size}px;color:${j.color || 'inherit'}}</style></head><body>${j.svg}</body></html>`
  await page.setContent(html)
  await page.screenshot({ path: path.resolve(root, j.out), omitBackground: !j.bg, clip: { x: 0, y: 0, width: j.size, height: j.size } })
  await page.close()
  console.log('wrote', j.out)
}
await browser.close()
