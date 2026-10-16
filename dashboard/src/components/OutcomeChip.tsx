import type { PairOutcome } from '../api/types'
import { OUTCOME_LABEL } from '../copy'
import { OutcomeMark } from './Marks'

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
  const s = size === 'sm' ? 'h-5 px-1.5 text-meta gap-1' : 'h-6 px-2 text-meta gap-1.5'
  return (
    <span
      role="img"
      aria-label={`Outcome: ${OUTCOME_LABEL[outcome]}`}
      data-outcome={outcome}
      className={`inline-flex shrink-0 items-center whitespace-nowrap border font-medium ${s} ${OUTCOME_TONE[outcome]} ${className}`}
    >
      <OutcomeMark outcome={outcome} />
      <span aria-hidden="true" className="leading-none">
        {OUTCOME_LABEL[outcome]}
      </span>
    </span>
  )
}
