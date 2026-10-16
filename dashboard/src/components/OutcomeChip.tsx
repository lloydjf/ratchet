import type { PairOutcome } from '../api/types'
import { OUTCOME_GLYPH, OUTCOME_LABEL } from '../copy'

export const OUTCOME_TONE: Record<PairOutcome, string> = {
  pass: 'tint-pass',
  improved: 'tint-pass',
  regression: 'tint-block',
  drift: 'tint-drift',
  uncertain: 'tint-warn',
  failed: 'tint-neutral',
}

/** Pair outcome with its short label ("Got worse", "World changed", …). */
export function OutcomeChip({ outcome, size = 'md', className = '' }: { outcome: PairOutcome; size?: 'sm' | 'md'; className?: string }) {
  const s = size === 'sm' ? 'h-6 px-2 text-mono gap-1.5' : 'h-7 px-2.5 text-meta gap-1.5'
  return (
    <span
      role="img"
      aria-label={`Outcome: ${OUTCOME_LABEL[outcome]}`}
      data-outcome={outcome}
      className={`inline-flex shrink-0 items-center whitespace-nowrap rounded-sm border font-medium ${s} ${OUTCOME_TONE[outcome]} ${className}`}
    >
      <span aria-hidden="true" className="font-mono font-semibold leading-none">
        {OUTCOME_GLYPH[outcome]}
      </span>
      <span aria-hidden="true" className="leading-none">
        {OUTCOME_LABEL[outcome]}
      </span>
    </span>
  )
}
