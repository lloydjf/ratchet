import { ArrowUp, Check, ChevronRight, CircleHelp, Minus, Pause, Waves, X, type LucideIcon } from 'lucide-react'
import type { Decision, OpinionVerdict, PairOutcome } from '../api/types'

/** Drawn marks in the rail icons' stroke (lucide, 2 px on a 24 grid), never Unicode glyphs. */
const DECISION_ICON: Record<Decision, LucideIcon> = { BLOCK: X, WARN: Pause, PASS: Check }
const OUTCOME_ICON: Record<PairOutcome, LucideIcon> = { pass: Check, regression: X, drift: Waves, improved: ArrowUp, uncertain: CircleHelp, failed: Minus }
const OPINION_ICON: Record<OpinionVerdict, LucideIcon> = { equivalent: Check, regressed: X, improved: ArrowUp, uncertain: CircleHelp }

type MarkProps = { size?: number; className?: string; strokeWidth?: number }

export function DecisionMark({ decision, size = 12, className = '', strokeWidth = 2.25 }: MarkProps & { decision: Decision }) {
  const Icon = DECISION_ICON[decision]
  return <Icon size={size} strokeWidth={strokeWidth} aria-hidden="true" className={`shrink-0 ${className}`} />
}
export function OutcomeMark({ outcome, size = 12, className = '', strokeWidth = 2.25 }: MarkProps & { outcome: PairOutcome }) {
  const Icon = OUTCOME_ICON[outcome]
  return <Icon size={size} strokeWidth={strokeWidth} aria-hidden="true" className={`shrink-0 ${className}`} />
}
export function OpinionMark({ verdict, size = 12, className = '', strokeWidth = 2.25 }: MarkProps & { verdict: OpinionVerdict }) {
  const Icon = OPINION_ICON[verdict]
  return <Icon size={size} strokeWidth={strokeWidth} aria-hidden="true" className={`inline shrink-0 ${className}`} />
}
/** "Then": the step between two values or two stages. */
export function StepMark({ size = 12, className = '' }: MarkProps) {
  return <ChevronRight size={size} strokeWidth={2} aria-hidden="true" className={`inline shrink-0 text-muted ${className}`} />
}
