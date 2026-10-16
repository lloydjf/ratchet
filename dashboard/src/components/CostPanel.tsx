import type { TierMap } from '../api/types'
import { TIERS } from '../api/types'
import { COST_SAVED, TIER_NAME } from '../copy'
import { ratio, ratioLabel, tokens, usd } from '../lib/format'

interface CostPanelProps {
  usdByTier: TierMap<number>
  tokensByTier: TierMap<number>
  counterfactualUsd: number
  /** Agent spend replaying the tasks (same tier as the original runs); not part of judging. */
  replayUsdByTier?: Partial<TierMap<number>>
  title?: string
  className?: string
}

export function savingsLine(actual: number, counterfactual: number): string | null {
  if (actual <= 0 || counterfactual <= actual) return null
  return COST_SAVED(usd(counterfactual - actual), ratioLabel(ratio(actual, counterfactual)))
}

/** A 1-px rule filled to the value, on the panel's shared scale. */
function Rule({ value, max, color }: { value: number; max: number; color: string }) {
  return (
    <span className="col-span-3 block h-[3px] bg-border" aria-hidden="true">
      <span className="block h-full" style={{ width: `${Math.max(value > 0 ? 1 : 0, (value / max) * 100)}%`, background: color }} />
    </span>
  )
}

/** Cost by tier with token counts, the all-Ultra counterfactual, and the savings line. */
export function CostPanel({ usdByTier, tokensByTier, counterfactualUsd, replayUsdByTier, title = 'Cost of this verdict', className = '' }: CostPanelProps) {
  const total = TIERS.reduce((a, t) => a + (usdByTier[t] ?? 0), 0)
  const replayTiers = TIERS.filter((t) => (replayUsdByTier?.[t] ?? 0) > 0)
  const max = Math.max(counterfactualUsd, total, 0.000001)
  const saved = savingsLine(total, counterfactualUsd)
  return (
    <section className={`flex flex-col ${className}`} aria-labelledby="cost-panel-title" data-testid="cost-panel">
      <h2 id="cost-panel-title" className="heading-sm">
        {title}
      </h2>
      <ul className="mt-3 flex flex-col gap-2.5">
        {TIERS.map((t) => {
          const v = usdByTier[t] ?? 0
          return (
            <li key={t} className="grid grid-cols-[1fr_auto_auto] items-baseline gap-x-3 gap-y-1">
              <span className="min-w-0 text-meta" style={{ color: `var(--${t}-ink)` }}>
                {TIER_NAME[t]}
              </span>
              <span className="num text-meta text-muted">
                <span className="font-mono">{tokens(tokensByTier[t])}</span> tok
              </span>
              <span className="num w-16 text-right font-mono text-mono text-text">{usd(v)}</span>
              <Rule value={v} max={max} color={`var(--${t})`} />
            </li>
          )
        })}
      </ul>
      <dl className="mt-4 grid grid-cols-[1fr_auto] gap-y-1.5 border-t border-border pt-3 text-meta">
        <dt className="font-medium text-text">Judging total</dt>
        <dd className="num text-right font-mono text-text">{usd(total)}</dd>
        <dt className="text-muted">If Nemotron Ultra judged everything</dt>
        <dd className="num text-right font-mono text-muted">{usd(counterfactualUsd)}</dd>
      </dl>
      <span className="mt-2 grid grid-cols-3">
        <Rule value={counterfactualUsd} max={max} color="var(--text-muted)" />
      </span>
      {saved ? (
        <p className="mt-3 text-meta font-medium text-pass-ink" data-testid="cost-saved">
          {saved}
        </p>
      ) : null}
      {replayTiers.length > 0 ? (
        <dl className="mt-3 grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 border-t border-border pt-3 text-meta" data-testid="replay-cost">
          <dt className="text-muted">Agent replays (same model as the original runs)</dt>
          <dd className="num text-right text-muted">{replayTiers.map((t) => `${usd(replayUsdByTier?.[t] ?? 0)} (${t})`).join(' + ')}</dd>
        </dl>
      ) : null}
    </section>
  )
}
