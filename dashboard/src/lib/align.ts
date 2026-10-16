import type { Step } from '../api/types'

export interface AlignedRow {
  golden: Step | null
  replay: Step | null
  differs: boolean
}

const sig = (s: Step) => (s.kind === 'llm' ? 'llm' : `tool:${s.tool}`)

function same(a: Step, b: Step): boolean {
  if (a.kind === 'llm' && b.kind === 'llm') return a.content_preview === b.content_preview
  if (a.kind === 'tool' && b.kind === 'tool') {
    // A read with the same result is not a behavioral difference, even if its arguments moved.
    if (a.side_effect === 'read' && b.side_effect === 'read') return a.result_hash === b.result_hash
    return a.args_hash === b.args_hash && a.result_hash === b.result_hash && a.blocked === b.blocked
  }
  return false
}

/** Align golden and replay steps by LCS over (kind, tool); unmatched steps become gaps. */
export function alignSteps(golden: Step[], replay: Step[]): AlignedRow[] {
  const n = golden.length
  const m = replay.length
  const dp: number[][] = Array.from({ length: n + 1 }, () => new Array<number>(m + 1).fill(0))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--)
      dp[i]![j] = sig(golden[i]!) === sig(replay[j]!) ? dp[i + 1]![j + 1]! + 1 : Math.max(dp[i + 1]![j]!, dp[i]![j + 1]!)
  const rows: AlignedRow[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    const g = golden[i]!
    const r = replay[j]!
    if (sig(g) === sig(r)) {
      rows.push({ golden: g, replay: r, differs: !same(g, r) })
      i++
      j++
    } else if (dp[i + 1]![j]! >= dp[i]![j + 1]!) {
      rows.push({ golden: g, replay: null, differs: true })
      i++
    } else {
      rows.push({ golden: null, replay: r, differs: true })
      j++
    }
  }
  while (i < n) rows.push({ golden: golden[i++]!, replay: null, differs: true })
  while (j < m) rows.push({ golden: null, replay: replay[j++]!, differs: true })
  return rows
}

export interface PathChange {
  path: string
  before: unknown
  after: unknown
}

const MISSING = Symbol('missing')

/** Leaf-level differences between two JSON values, e.g. `rows[0].vat: 59.28 → missing`. */
export function diffPaths(a: unknown, b: unknown, path = '', out: PathChange[] = [], limit = 6): PathChange[] {
  if (out.length >= limit) return out
  const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object'
  if (Array.isArray(a) && Array.isArray(b)) {
    for (let i = 0; i < Math.max(a.length, b.length); i++) diffPaths(i < a.length ? a[i] : MISSING, i < b.length ? b[i] : MISSING, `${path}[${i}]`, out, limit)
    return out
  }
  if (isObj(a) && isObj(b) && !Array.isArray(a) && !Array.isArray(b)) {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) diffPaths(k in a ? a[k] : MISSING, k in b ? b[k] : MISSING, path ? `${path}.${k}` : k, out, limit)
    return out
  }
  if (JSON.stringify(a) !== JSON.stringify(b)) out.push({ path: path || '(value)', before: a === MISSING ? undefined : a, after: b === MISSING ? undefined : b })
  return out
}
