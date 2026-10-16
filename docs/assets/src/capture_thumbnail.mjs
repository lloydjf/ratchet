// Devpost thumbnail (1280x720): the github-triage BLOCK verdict, with the decision chip, the headline
// and one replayed task's judge trail, rendered at 2x so no text is under 24 px (smallest UI text is 12 px).
// It is the live verdict page at a 640x360 viewport; sections between the header and the chosen replay
// are hidden (display:none) so both fit in one frame. Nothing is restyled; no model is called.
// Usage: node docs/assets/src/capture_thumbnail.mjs [baseUrl] [pairText]
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const require = createRequire(path.join(root, 'dashboard/package.json'))
const { chromium } = require('@playwright/test')
const BASE = process.argv[2] || 'http://127.0.0.1:8090'
const PAIR = process.argv[3] || 'acme/billing-api'
const OUT = process.env.OUT || path.join(root, 'docs/assets/thumbnail-1280x720.png')

const browser = await chromium.launch()
const ctx = await browser.newContext({ viewport: { width: 640, height: 360 }, deviceScaleFactor: 2, colorScheme: 'dark', reducedMotion: 'reduce' })
await ctx.route('**/api/v1/demo/events', (r) => r.fulfill({ status: 204, body: '' }))
const page = await ctx.newPage()
await page.goto(BASE + '/verdicts/verdict_fa80c0517746', { waitUntil: 'networkidle' })
await page.evaluate(() => document.fonts.ready)
await page.waitForTimeout(600)
const info = await page.evaluate(({ pairText, keepNav }) => {
  const hide = (el) => el && (el.style.display = 'none')
  const siteHeader = document.querySelector('header.sticky')
  if (!keepNav) hide(siteHeader)
  else hide(siteHeader.querySelector('nav.md\\:hidden, nav[class*="md:hidden"]'))
  hide(document.querySelector('footer'))
  const main = document.querySelector('main')
  const card = main.querySelector('header')
  hide(card.querySelector('dl'))
  hide([...card.querySelectorAll('p')].find((p) => /Actions are available/.test(p.textContent)))
  const grid = card.nextElementSibling // narrative/diff/pairs + aside
  hide(grid.querySelector('aside'))
  const col = grid.firstElementChild
  const pairsSection = [...col.children].find((s) => /Replayed tasks/.test(s.textContent))
  for (const s of col.children) if (s !== pairsSection) hide(s)
  // keep only the chosen replay card
  const cands = [...pairsSection.querySelectorAll('*')].filter((e) => e.textContent.includes(pairText) && /Got worse|Same result/.test(e.textContent))
  const cardEl = cands.filter((e) => getComputedStyle(e).borderTopWidth !== '0px').sort((a, b) => a.textContent.length - b.textContent.length)[0]
  let keep = cardEl
  while (keep && keep.parentElement !== pairsSection) {
    for (const sib of keep.parentElement.children) if (sib !== keep) hide(sib)
    keep = keep.parentElement
  }
  for (const sib of pairsSection.children) if (sib !== keep) hide(sib)
  cardEl.style.margin = '0'
  // tighten the vertical rhythm between the two remaining blocks
  // pull the replay card up: the hidden sections leave the column's own gaps behind
  for (const el of [main.firstElementChild, grid, col, pairsSection]) el.style.gap = '0'
  grid.style.marginTop = '20px'
  main.style.paddingTop = '0px'
  main.style.paddingBottom = '0px'
  window.scrollTo(0, 0)
  return {}
}, { pairText: PAIR, keepNav: process.env.KEEP_NAV === '1' })
await page.waitForTimeout(300)
// centre the remaining stack vertically in the frame, measured after layout settles
const info2 = await page.evaluate((keepNav) => {
  const main = document.querySelector('main')
  const navH = keepNav ? document.querySelector('header.sticky').getBoundingClientRect().height : 0
  const h = main.firstElementChild.getBoundingClientRect().height
  const pad = Math.max(0, (360 - navH - h) / 2)
  main.style.paddingTop = pad + 'px'
  return { navH, h, pad }
}, process.env.KEEP_NAV === '1')
Object.assign(info, info2)
console.log(info)
if (info.h + info.navH > 360) console.warn('content taller than the frame by', info.h + info.navH - 360, 'CSS px')
await page.waitForTimeout(200)
await page.screenshot({ path: OUT })
console.log('wrote', path.relative(root, OUT))
await browser.close()
