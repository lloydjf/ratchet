import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const tokensCss = readFileSync(resolve(__dirname, 'tokens.css'), 'utf8')
const appCss = readFileSync(resolve(__dirname, 'app.css'), 'utf8')

/**
 * Contrast rules: text ≥ 4.5:1 (chip text on its 15% tint), chip borders ≥ 3:1. Chips render their
 * border as `color-mix(in srgb, <hue> N%, var(--surface))` (app.css `.tint-*`), so the border
 * check resolves that exact expression — the same sRGB mix the browser does — instead of
 * testing the raw token hue.
 */

type RGB = [number, number, number]
type Tokens = Record<string, string>

/** Every `--name: value;` declaration in the first block matching `selector`. */
function block(css: string, selector: RegExp): Tokens {
  const m = css.match(selector)
  if (!m) throw new Error(`no block for ${selector}`)
  const out: Tokens = {}
  for (const [, k, v] of m[1]!.matchAll(/--([a-z0-9-]+):\s*([^;]+);/gi)) out[k!] = v!.trim()
  return out
}

const dark = block(tokensCss, /:root \{([\s\S]*?)\n\}/)
const lightExplicit = block(tokensCss, /:root\[data-theme='light'\] \{([\s\S]*?)\n\}/)
const light = { ...dark, ...lightExplicit }

const hexRgb = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB

/** Split a CSS argument list on top-level commas. */
function args(s: string): string[] {
  const out: string[] = []
  let depth = 0
  let cur = ''
  for (const ch of s) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (ch === ',' && depth === 0) {
      out.push(cur.trim())
      cur = ''
    } else cur += ch
  }
  out.push(cur.trim())
  return out
}

/** Resolve `#hex`, `var(--x)` and `color-mix(in srgb, A p%, B)` to an sRGB triple (0–255, unrounded). */
function color(expr: string, t: Tokens, depth = 0): RGB {
  if (depth > 10) throw new Error(`cycle resolving ${expr}`)
  const e = expr.trim()
  if (/^#[0-9a-f]{6}$/i.test(e)) return hexRgb(e)
  const v = e.match(/^var\(--([a-z0-9-]+)\)$/i)
  if (v) {
    const val = t[v[1]!]
    if (!val) throw new Error(`unknown token --${v[1]}`)
    return color(val, t, depth + 1)
  }
  const m = e.match(/^color-mix\((.*)\)$/i)
  if (m) {
    const [space, a, b] = args(m[1]!)
    if (space !== 'in srgb') throw new Error(`only srgb mixes are modelled: ${e}`)
    const [ca, pa] = part(a!, t, depth)
    const [cb, pb] = part(b!, t, depth)
    // CSS Color 5: a missing share is 100% minus the other; shares are normalised to 1.
    const p1 = pa ?? (pb != null ? 1 - pb : 0.5)
    const p2 = pb ?? 1 - p1
    const sum = p1 + p2
    return ca.map((x, i) => (x * p1 + cb[i]! * p2) / sum) as RGB
  }
  throw new Error(`cannot resolve color ${e}`)
}

function part(s: string, t: Tokens, depth: number): [RGB, number | null] {
  const m = s.match(/^(.*?)\s+(\d+(?:\.\d+)?%|var\(--[a-z0-9-]+\))$/i)
  if (!m) return [color(s, t, depth + 1), null]
  let pct = m[2]!
  const pv = pct.match(/^var\(--([a-z0-9-]+)\)$/i)
  if (pv) pct = t[pv[1]!] ?? ''
  if (!/^\d+(?:\.\d+)?%$/.test(pct)) throw new Error(`bad mix share ${m[2]}`)
  return [color(m[1]!, t, depth + 1), parseFloat(pct) / 100]
}

const lum = (c: RGB) => {
  const [r, g, b] = c.map((v) => {
    const s = v / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!
}
const ratio = (a: RGB, b: RGB) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
  return (x! + 0.05) / (y! + 0.05)
}

/** `.tint-<tone>` rules from app.css: { background, color, border-color } expressions. */
function chipRules(): Record<string, Record<string, string>> {
  const out: Record<string, Record<string, string>> = {}
  for (const [, tone, body] of appCss.matchAll(/\.tint-([a-z]+)\s*\{([^}]*)\}/g)) {
    const decl: Record<string, string> = {}
    for (const [, k, v] of body!.matchAll(/([a-z-]+):\s*([^;]+);/g)) decl[k!] = v!.trim()
    out[tone!] = decl
  }
  return out
}
const CHIPS = chipRules()
const HUES = ['pass', 'warn', 'block', 'drift', 'nano', 'super', 'ultra'] as const
const TONES = [...HUES, 'neutral'] as const

describe('contrast test harness', () => {
  it('finds a .tint-* rule with a border for every chip tone', () => {
    for (const tone of TONES) expect(CHIPS[tone]?.['border-color'], tone).toBeTruthy()
  })
  it('mixes in sRGB the way CSS does', () => {
    const t = { a: '#000000', b: '#ffffff', p: '25%' }
    expect(color('color-mix(in srgb, var(--a) 25%, var(--b))', t)).toEqual([191.25, 191.25, 191.25])
    expect(color('color-mix(in srgb, var(--a) var(--p), var(--b))', t)).toEqual([191.25, 191.25, 191.25])
    expect(color('color-mix(in srgb, var(--a), var(--b))', t)).toEqual([127.5, 127.5, 127.5])
  })
  it('ships dark by default: light is only the explicit choice, never the system setting', () => {
    expect(tokensCss).not.toMatch(/prefers-color-scheme/)
    expect(Object.keys(lightExplicit).length).toBeGreaterThan(10)
  })
})

for (const [mode, t] of [
  ['dark', dark],
  ['light', light],
] as const) {
  describe(`${mode} tokens`, () => {
    const surface = color('var(--surface)', t)
    it.each(HUES)('%s chip text clears 4.5:1 on its tint', (h) => {
      const rule = CHIPS[h]!
      expect(ratio(color(rule.color!, t), color(rule.background!, t))).toBeGreaterThanOrEqual(4.5)
    })
    it.each(['text', 'text-muted', 'accent'])('%s clears 4.5:1 on bg and surface', (k) => {
      expect(ratio(color(`var(--${k})`, t), color('var(--bg)', t))).toBeGreaterThanOrEqual(4.5)
      expect(ratio(color(`var(--${k})`, t), surface)).toBeGreaterThanOrEqual(4.5)
    })
    it.each(TONES)('%s chip border, as rendered, clears 3:1 on surface', (tone) => {
      const rendered = color(CHIPS[tone]!['border-color']!, t)
      expect(ratio(rendered, surface)).toBeGreaterThanOrEqual(3)
    })
  })
}
