import type { TierMap } from '../api/types'
import { TIERS } from '../api/types'
import { TIER_LETTER, TIER_SHORT } from '../copy'
import { usd } from '../lib/format'
import { useAnimatedNumber } from '../lib/useAnimatedNumber'
import { usePrefersReducedMotion } from '../lib/usePrefersReducedMotion'

interface CostTickerProps {
  usdByTier: TierMap<number>
  live: boolean
  label?: string
  className?: string
}

/** Spend by tier: three mini bars and a total. Counts (300 ms) only while live. */
export function CostTicker({ usdByTier, live, label = 'Spend today', className = '' }: CostTickerProps) {
  const reduced = usePrefersReducedMotion()
  const total = TIERS.reduce((a, t) => a + (usdByTier[t] ?? 0), 0)
  const shown = useAnimatedNumber(total, live && !reduced)
  const max = Math.max(...TIERS.map((t) => usdByTier[t] ?? 0), 0.000001)
  const summary = `${label}: ${usd(total)}. ${TIERS.map((t) => `${TIER_SHORT[t]} ${usd(usdByTier[t] ?? 0)}`).join(', ')}.`
  return (
    <div
      className={`inline-flex h-10 items-center gap-3 rounded-sm border border-border bg-surface px-3 ${className}`}
      data-testid="cost-ticker"
      data-live={live || undefined}
      role="group"
      aria-label={summary}
    >
      <div className="flex h-5 items-end gap-[3px]" aria-hidden="true">
        {TIERS.map((t) => {
          const v = usdByTier[t] ?? 0
          const h = v > 0 ? Math.max(3, Math.round((v / max) * 20)) : 2
          return (
            <span key={t} className="flex w-[7px] flex-col items-center justify-end" title={`${TIER_SHORT[t]} ${usd(v)}`}>
              <span
                className="block w-full rounded-[2px] transition-[height] duration-300 ease-out"
                style={{ height: h, background: v > 0 ? `var(--${t})` : 'var(--border)' }}
              />
            </span>
          )
        })}
      </div>
      <div className="flex flex-col leading-none" aria-hidden="true">
        <span className="text-[11px] leading-[14px] text-muted">
          {label}
          {live ? <span className="ml-1.5 text-pass-ink">● live</span> : null}
        </span>
        <span className="num font-mono text-meta font-semibold text-text">{usd(shown)}</span>
      </div>
      <div className="hidden items-center gap-2 border-l border-border pl-3 font-mono text-[11px] leading-[14px] text-muted xl:flex" aria-hidden="true">
        {TIERS.map((t) => (
          <span key={t} className="num">
            <span style={{ color: `var(--${t}-ink)` }} className="font-bold">
              {TIER_LETTER[t]}
            </span>{' '}
            {usd(usdByTier[t] ?? 0)}
          </span>
        ))}
      </div>
    </div>
  )
}
