import type { PairOutcome, PairVerdict, SideEffectClass, Tier } from '../api/types'
import { HARD_SIDE_EFFECTS, TIERS } from '../api/types'
import { level0Marker, OUTPUT_SIMILARITY_FLOOR } from '../copy'

/** Problems-first order: regression, uncertain, drift, improved, pass (failed sits with the problems). */
export const OUTCOME_ORDER: PairOutcome[] = ['regression', 'uncertain', 'failed', 'drift', 'improved', 'pass']

export function sortPairs(pairs: PairVerdict[]): PairVerdict[] {
  return [...pairs].sort((a, b) => {
    const d = OUTCOME_ORDER.indexOf(a.outcome) - OUTCOME_ORDER.indexOf(b.outcome)
    if (d) return d
    return (a.task_label || a.task_input).localeCompare(b.task_label || b.task_input)
  })
}

export type Level0Kind = 'none' | 'changed' | 'hard'

export function level0Kind(p: PairVerdict): Level0Kind {
  if (p.level0.hard_fail) return 'hard'
  if (hasStructuralFinding(p)) return 'changed'
  return 'none'
}

/** Level 0 found a missing, new or re-argued side-effect call (any class). */
export function hasStructuralFinding(p: Pick<PairVerdict, 'level0'>): boolean {
  const l = p.level0
  return l.changed_args.length > 0 || l.new_calls.length > 0 || l.missing_calls.length > 0
}

/**
 * ADR 0024: Level 0 kept Nano from clearing this pair alone (a structural finding, or final
 * outputs under 90 % alike), so the decision came from a later stage. Not said for pairs
 * Level 0 itself decided or that never reached a judge.
 */
export function nanoGatedByLevel0(p: Pick<PairVerdict, 'level0' | 'decided_by'>): boolean {
  if (p.decided_by === 'nano' || p.decided_by === 'level0' || p.decided_by === 'infra') return false
  return hasStructuralFinding(p) || (p.level0.output_similarity ?? 1) < OUTPUT_SIMILARITY_FLOOR
}

/** "Checked without a model: {n} new {class} call(s)." for the dominant hard class. */
export function level0Copy(p: PairVerdict): string | null {
  const hard = p.level0.new_calls.filter((c) => HARD_SIDE_EFFECTS.has(c.side_effect))
  if (!hard.length) return null
  const counts = new Map<SideEffectClass, number>()
  for (const c of hard) counts.set(c.side_effect, (counts.get(c.side_effect) ?? 0) + 1)
  const [cls, n] = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]!
  return level0Marker(n, cls)
}

export function changedArgsCopy(p: PairVerdict): string | null {
  const n = p.level0.changed_args.length
  if (!n) return null
  const tools = [...new Set(p.level0.changed_args.map(([, r]) => r.tool))].join(', ')
  return `Same ${tools} call${n === 1 ? '' : 's'} with different arguments.`
}

export function pairTiers(p: PairVerdict): Tier[] {
  return TIERS.filter((t) => p.opinions.some((o) => o.tier === t))
}

/** Cost with its tier(s): "$0.0006 (nano)", "$0.027 (nano, super, ultra)", "$0.00 (no model)". */
export function pairCostTiers(p: PairVerdict): string {
  const tiers = pairTiers(p)
  if (!tiers.length) return 'no model'
  return tiers.join(', ')
}

export const pairKey = (p: Pick<PairVerdict, 'golden_run_id' | 'replay_run_id'>) => `${p.golden_run_id}/${p.replay_run_id}`
