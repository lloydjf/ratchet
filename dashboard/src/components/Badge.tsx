import type { ReactNode } from 'react'
import { Tooltip } from './Tooltip'

export type BadgeTone = 'neutral' | 'pass' | 'warn' | 'block' | 'drift' | 'accent'

const TONE: Record<BadgeTone, string> = {
  neutral: 'tint-neutral',
  pass: 'tint-pass',
  warn: 'tint-warn',
  block: 'tint-block',
  drift: 'tint-drift',
  accent: 'tint-drift',
}

interface BadgeProps {
  text: string
  tone?: BadgeTone
  hint?: ReactNode
  icon?: ReactNode
  className?: string
}

/** Status badge ("Recorded run", "Truncated", "Drift check unavailable", "Simulated models"). */
export function Badge({ text, tone = 'neutral', hint, icon, className = '' }: BadgeProps) {
  const cls = `inline-flex h-5 items-center gap-1.5 whitespace-nowrap border px-1.5 text-meta font-medium ${TONE[tone]} ${className}`
  if (!hint)
    return (
      <span className={cls} data-badge={text}>
        {icon}
        {text}
      </span>
    )
  return (
    <Tooltip content={hint}>
      {(t) => (
        <span tabIndex={0} className={`${cls} cursor-help`} data-badge={text} aria-describedby={t['aria-describedby']} onFocus={t.onFocus} onBlur={t.onBlur}>
          {icon}
          {text}
        </span>
      )}
    </Tooltip>
  )
}
