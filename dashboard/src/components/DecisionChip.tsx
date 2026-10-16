import type { Decision } from '../api/types'
import { DECISION_ARIA, DECISION_GLYPH, DECISION_WORD } from '../copy'

export type ChipSize = 'sm' | 'md' | 'lg'

export const DECISION_TONE: Record<Decision, 'pass' | 'warn' | 'block'> = { PASS: 'pass', WARN: 'warn', BLOCK: 'block' }

const SIZE: Record<ChipSize, string> = {
  sm: 'h-6 px-2 gap-1.5 text-mono font-semibold tracking-[0.06em]',
  md: 'h-8 px-3 gap-2 text-meta font-semibold tracking-[0.06em]',
  lg: 'h-11 px-4 gap-2.5 text-h3 font-bold tracking-[0.08em]',
}

interface DecisionChipProps {
  decision: Decision
  size?: ChipSize
  /** Muted: expected but not yet decided (landing card before a run). */
  muted?: boolean
  /** Play the 240 ms fill when the decision first appears. */
  animate?: boolean
  className?: string
}

/** BLOCK / WARN / PASS with its text glyph (✓ ! ✕); color never carries meaning alone. */
export function DecisionChip({ decision, size = 'md', muted = false, animate = false, className = '' }: DecisionChipProps) {
  const tone = muted ? 'tint-neutral border-dashed' : `tint-${DECISION_TONE[decision]}`
  const label = muted ? `Expected decision: ${DECISION_WORD[decision]}` : DECISION_ARIA[decision]
  return (
    <span
      role="img"
      aria-label={label}
      data-decision={decision}
      className={`inline-flex shrink-0 items-center rounded-sm border select-none num ${SIZE[size]} ${tone} ${animate ? 'anim-chip-fill' : ''} ${className}`}
    >
      <span aria-hidden="true" className="font-mono leading-none">
        {DECISION_GLYPH[decision]}
      </span>
      <span aria-hidden="true" className="leading-none">
        {DECISION_WORD[decision]}
      </span>
    </span>
  )
}
