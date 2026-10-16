/**
 * Which diff hunk a pair's difference traces to. A hunk is linked to a pair when the pair's
 * own evidence (Level 0 calls and arguments, the judges' reasons, the ruling) names
 * something the hunk changed: a code span, a dotted or snake_case identifier, an acronym,
 * or at least two of its longer words. A diff with a single hunk owns every difference.
 * World drift and nondeterminism (a diverging control) are not the edit's doing, so they
 * are never attributed to a hunk.
 */
import type { PairVerdict } from '../api/types'
import type { DiffFile, DiffLine } from '../components/DiffViewer'
import { hasStructuralFinding, pairKey } from './pairs'

export interface Hunk {
  id: string
  file: string
  header: string
  lines: DiffLine[]
}

export function hunksOf(files: DiffFile[]): Hunk[] {
  const out: Hunk[] = []
  for (const f of files) {
    let cur: Hunk | null = null
    for (const l of f.lines) {
      if (l.type === 'hunk') {
        cur = { id: `${f.path}#${out.length}`, file: f.path, header: l.text, lines: [] }
        out.push(cur)
      } else if (cur) cur.lines.push(l)
    }
  }
  return out
}

const STOP = new Set(['about', 'after', 'again', 'their', 'there', 'these', 'those', 'which', 'while', 'would', 'should', 'could', 'other', 'every', 'never', 'always', 'before', 'being', 'where', 'skill', 'skills', 'their', 'using', 'issue', 'issues', 'tasks', 'label', 'labels', 'items', 'lines'])

export function hunkTokens(h: Hunk): { strong: Set<string>; weak: Set<string> } {
  const strong = new Set<string>()
  const weak = new Set<string>()
  for (const l of h.lines) {
    if (l.type !== 'add' && l.type !== 'del') continue
    for (const m of l.text.matchAll(/`([^`]+)`/g)) for (const w of m[1]!.match(/[A-Za-z_][\w.]*/g) ?? []) if (w.length >= 3) strong.add(w.toLowerCase())
    for (const w of l.text.match(/\b[a-z]+(?:[._][a-z]+)+\b/gi) ?? []) strong.add(w.toLowerCase())
    for (const w of l.text.match(/\b[A-Z]{2,}\b/g) ?? []) strong.add(w.toLowerCase())
    for (const w of l.text.toLowerCase().match(/\b[a-z]{5,}\b/g) ?? []) if (!STOP.has(w)) weak.add(w)
  }
  return { strong, weak }
}

/** Everything a pair's verdict says about what differed, lowercased. */
export function pairEvidence(p: PairVerdict): string {
  const calls = [...p.level0.new_calls, ...p.level0.missing_calls, ...p.level0.changed_args.flat()]
  return [
    p.reason,
    ...calls.map((c) => `${c.tool} ${JSON.stringify(c.args_norm)}`),
    ...p.opinions.flatMap((o) => [...o.reasons, o.causal_explanation ?? '', o.failure_summary ?? '', ...o.criteria_met.filter((c) => !c.met).map((c) => c.criterion)]),
  ]
    .join(' \n ')
    .toLowerCase()
}

/** The pair differs from its golden in a way the edit could have caused. */
export function attributable(p: PairVerdict): boolean {
  if (p.outcome === 'drift' || p.decided_by === 'drift' || p.decided_by === 'control' || p.decided_by === 'infra') return false
  if (p.outcome !== 'pass') return true
  return hasStructuralFinding(p) || (p.level0.output_similarity ?? 1) < 0.999 || p.opinions.some((o) => o.verdict !== 'equivalent')
}

const has = (hay: string, w: string) => new RegExp(`(^|[^a-z0-9_])${w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}($|[^a-z0-9_])`).test(hay)

/** Pair keys per hunk id. */
export function hunkPairs(hunks: Hunk[], pairs: readonly PairVerdict[]): Map<string, string[]> {
  const out = new Map<string, string[]>(hunks.map((h) => [h.id, []]))
  const candidates = pairs.filter(attributable)
  if (hunks.length === 1) {
    out.set(hunks[0]!.id, candidates.map(pairKey))
    return out
  }
  for (const h of hunks) {
    const { strong, weak } = hunkTokens(h)
    for (const p of candidates) {
      const ev = pairEvidence(p)
      const strongHit = [...strong].some((w) => has(ev, w))
      const weakHits = [...weak].filter((w) => has(ev, w)).length
      if (strongHit || weakHits >= 2) out.get(h.id)!.push(pairKey(p))
    }
  }
  return out
}
