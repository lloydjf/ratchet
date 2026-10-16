import type { Decision } from '../api/types'
import { DECISION_ARIA, DECISION_CHIP, DECISION_WORD } from '../copy'
import { DecisionMark } from './Marks'

export type ChipSize = 'sm' | 'md' | 'lg'

export const DECISION_TONE: Record<Decision, 'pass' | 'warn' | 'block'> = { PASS: 'pass', WARN: 'warn', BLOCK: 'block' }

const SIZE: Record<ChipSize, string> = {
  sm: 'h-5 px-1.5 gap-1 text-meta font-medium',
  md: 'h-6 px-2 gap-1.5 text-meta font-medium',
  lg: 'h-8 px-2.5 gap-2 text-body font-semibold',
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
      className={`inline-flex shrink-0 items-center border select-none num ${SIZE[size]} ${tone} ${animate ? 'anim-stage' : ''} ${className}`}
    >
      <DecisionMark decision={decision} size={size === 'lg' ? 14 : 12} />
      <span aria-hidden="true" className="leading-none">
        {DECISION_CHIP[decision]}
      </span>
    </span>
  )
}
