import { useState } from 'react'
import type { Tier, TierMap } from '../api/types'
import { TIERS } from '../api/types'
import { TICKER, TIER_LETTER, TIER_SHORT } from '../copy'
import { usd } from '../lib/format'
import { useAnimatedNumber } from '../lib/useAnimatedNumber'
import { usePrefersReducedMotion } from '../lib/usePrefersReducedMotion'

interface CostTickerProps {
  usdByTier: TierMap<number>
  live: boolean
  /** What the same work would have cost with Ultra judging everything; shown on hover. */
  counterfactualUsd?: number | null
  label?: string
  /** `rail`: the full ticker; `compact`: bars only (the icon rail). */
  variant?: 'rail' | 'compact'
  className?: string
}

const TICKS = 40

/** A hairline bar-field: one 1-px tick per 2.5 % of the scale, filled up to the value. */
function BarField({ value, max, color, muted = false, width = 112 }: { value: number; max: number; color: string; muted?: boolean; width?: number }) {
  const filled = max > 0 ? Math.round((Math.min(value, max) / max) * TICKS) : 0
  const pitch = width / TICKS
  return (
    <svg width={width} height={8} aria-hidden="true" className="block shrink-0">
      {Array.from({ length: TICKS }, (_, i) => (
        <line
          key={i}
          x1={i * pitch + 0.5}
          x2={i * pitch + 0.5}
          y1={0}
          y2={8}
          stroke={i < filled || (i === 0 && value > 0) ? color : 'var(--border)'}
          strokeOpacity={muted && i < filled ? 0.55 : 1}
          strokeWidth={1}
        />
      ))}
    </svg>
  )
}

function useTierNumbers(usdByTier: TierMap<number>, animate: boolean): Record<Tier, number> {
  const nano = useAnimatedNumber(usdByTier.nano ?? 0, animate)
  const sup = useAnimatedNumber(usdByTier.super ?? 0, animate)
  const ultra = useAnimatedNumber(usdByTier.ultra ?? 0, animate)
  return { nano, super: sup, ultra }
}

/**
 * Spend by tier, live: the total and each tier interpolate over 300 ms while a run is
 * spending; three hairline bar-fields share one scale. Hover or focus adds the all-Ultra
 * counterfactual as a muted fourth bar on the same scale.
 */
export function CostTicker({ usdByTier, live, counterfactualUsd, label = TICKER.label, variant = 'rail', className = '' }: CostTickerProps) {
  const reduced = usePrefersReducedMotion()
  const animate = live && !reduced
  const total = TIERS.reduce((a, t) => a + (usdByTier[t] ?? 0), 0)
  const shown = useAnimatedNumber(total, animate)
  const tiers = useTierNumbers(usdByTier, animate)
  const [peek, setPeek] = useState(false)
  const cf = counterfactualUsd != null && counterfactualUsd > total ? counterfactualUsd : null
  // At rest the bars compare the tiers; the all-Ultra bar rescales them over 300 ms when it joins.
  const restMax = Math.max(...TIERS.map((t) => usdByTier[t] ?? 0), 0.000001)
  const max = useAnimatedNumber(peek && cf ? Math.max(restMax, cf) : restMax, !reduced)
  const summary = `${label}: ${usd(total)}. ${TIERS.map((t) => `${TIER_SHORT[t]} ${usd(usdByTier[t] ?? 0)}`).join(', ')}.${cf ? ` ${TICKER.allUltra}: ${usd(cf)} (ultra, ${TICKER.estimate}).` : ''}`

  if (variant === 'compact')
    return (
      <div className={`flex flex-col items-center gap-1.5 ${className}`} data-testid="cost-ticker" data-live={live || undefined} role="group" aria-label={summary} title={summary}>
        {TIERS.map((t) => (
          <BarField key={t} value={usdByTier[t] ?? 0} max={restMax} color={`var(--${t})`} width={28} />
        ))}
        {live ? <span aria-hidden="true" className="block h-1.5 w-1.5 rounded-full bg-pass" /> : null}
      </div>
    )

  return (
    <div
      className={`flex flex-col gap-2 ${className}`}
      data-testid="cost-ticker"
      data-live={live || undefined}
      role="group"
      aria-label={summary}
      tabIndex={0}
      onMouseEnter={() => setPeek(true)}
      onMouseLeave={() => setPeek(false)}
      onFocus={() => setPeek(true)}
      onBlur={() => setPeek(false)}
    >
      <div className="flex items-baseline justify-between gap-2" aria-hidden="true">
        <span className="text-meta text-muted">{label}</span>
        {live ? <span className="text-meta font-medium text-pass-ink">{TICKER.live}</span> : null}
      </div>
      <p className="num font-mono text-h3 font-medium text-text" aria-hidden="true">
        {usd(shown)}
      </p>
      <ul className="flex flex-col gap-1.5" aria-hidden="true">
        {TIERS.map((t) => (
          <li key={t} className="grid grid-cols-[12px_1fr_auto] items-center gap-2">
            <span className="font-mono text-meta font-medium" style={{ color: `var(--${t}-ink)` }}>
              {TIER_LETTER[t]}
            </span>
            <BarField value={usdByTier[t] ?? 0} max={max} color={`var(--${t})`} />
            <span className="num font-mono text-meta text-muted">{usd(tiers[t])}</span>
          </li>
        ))}
        {peek && cf ? (
          <li className="grid grid-cols-[12px_1fr_auto] items-center gap-2" data-testid="ticker-counterfactual">
            <span aria-hidden="true" />
            <BarField value={cf} max={max} color="var(--text-muted)" muted />
            <span className="num font-mono text-meta text-muted">{usd(cf)}</span>
          </li>
        ) : null}
      </ul>
      {peek && cf ? (
        <p className="text-meta text-muted" aria-hidden="true">
          {TICKER.allUltra} ({TICKER.estimate})
        </p>
      ) : null}
    </div>
  )
}
