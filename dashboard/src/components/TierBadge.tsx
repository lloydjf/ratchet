import type { OpinionVerdict, Tier } from '../api/types'
import { SKIPPED_TIER, TIER_LETTER, TIER_NAME, tierTooltip } from '../copy'
import { OpinionMark } from './Marks'
import { Tooltip } from './Tooltip'

interface TierBadgeProps {
  tier: Tier
  verdict?: OpinionVerdict
  confidence?: number
  reasons?: string[]
  /** Dotted outline: this tier was not consulted. */
  skipped?: boolean
  size?: 'sm' | 'md'
  /** Plain badges (cost panel rows) have no tooltip. */
  plain?: boolean
}

/** N / S / U letter badge; with a verdict it shows the glyph and a keyboard-reachable tooltip. */
export function TierBadge({ tier, verdict, confidence, reasons, skipped = false, size = 'md', plain = false }: TierBadgeProps) {
  const dim = size === 'sm' ? 'h-5 min-w-5 text-meta' : 'h-6 min-w-6 text-meta'
  const tone = skipped ? 'border-dashed border-border-strong text-muted bg-transparent' : `tint-${tier}`
  const label = skipped
    ? `${TIER_NAME[tier]}: not needed`
    : verdict
      ? `${TIER_NAME[tier]}: ${verdict}${confidence != null ? `, ${Math.round(confidence * 100)}%` : ''}`
      : TIER_NAME[tier]
  const inner = (
    <>
      <span aria-hidden="true" className="font-mono font-medium leading-none">
        {TIER_LETTER[tier]}
      </span>
      {verdict && !skipped ? (
        <OpinionMark verdict={verdict} size={11} />
      ) : null}
    </>
  )
  const base = `inline-flex items-center justify-center gap-1 border px-1 ${dim} ${tone}`
  if (plain)
    return (
      <span role="img" aria-label={label} data-tier={tier} className={base}>
        {inner}
      </span>
    )
  const tip = skipped ? SKIPPED_TIER : verdict ? tierTooltip(tier, verdict, confidence ?? 0, reasons?.[0]) : TIER_NAME[tier]
  return (
    <Tooltip content={tip}>
      {(t) => (
        <button
          type="button"
          data-tier={tier}
          data-skipped={skipped || undefined}
          className={`${base} cursor-help`}
          {...t}
        >
          {inner}
          <span className="sr-only">{label}</span>
        </button>
      )}
    </Tooltip>
  )
}
