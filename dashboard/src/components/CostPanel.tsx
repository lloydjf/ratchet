import type { TierMap } from '../api/types'
import { TIERS } from '../api/types'
import { COST_SAVED, TIER_NAME } from '../copy'
import { ratio, ratioLabel, tokens, usd } from '../lib/format'
import { TierBadge } from './TierBadge'

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

/** Cost by tier with token counts, the all-Ultra counterfactual, and the savings line. */
export function CostPanel({
  usdByTier,
  tokensByTier,
  counterfactualUsd,
  replayUsdByTier,
  title = 'Cost of this verdict',
  className = '',
}: CostPanelProps) {
  const total = TIERS.reduce((a, t) => a + (usdByTier[t] ?? 0), 0)
  const replayTiers = TIERS.filter((t) => (replayUsdByTier?.[t] ?? 0) > 0)
  const max = Math.max(counterfactualUsd, total, 0.000001)
  const saved = savingsLine(total, counterfactualUsd)
  return (
    <section className={`card p-4 ${className}`} aria-labelledby="cost-panel-title" data-testid="cost-panel">
      <h2 id="cost-panel-title" className="eyebrow">
        {title}
      </h2>
      <ul className="mt-3 flex flex-col gap-3">
        {TIERS.map((t) => {
          const v = usdByTier[t] ?? 0
          return (
            <li key={t} className="grid grid-cols-[auto_1fr_auto] items-center gap-x-3 gap-y-1.5">
              <TierBadge tier={t} plain size="sm" />
              <span className="min-w-0 text-meta text-text">
                {TIER_NAME[t]}
                <span className="num ml-2 font-mono text-mono text-muted">{tokens(tokensByTier[t])} tok</span>
              </span>
              <span className="num font-mono text-meta text-text">{usd(v)}</span>
              <span className="col-span-3 block h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                <span className="block h-full rounded-full" style={{ width: `${Math.max(v > 0 ? 1.5 : 0, (v / max) * 100)}%`, background: `var(--${t})` }} />
              </span>
            </li>
          )
        })}
      </ul>
      <dl className="mt-4 grid grid-cols-[1fr_auto] gap-y-1.5 border-t border-border pt-3 text-meta">
        <dt className="text-text font-medium">Judging total</dt>
        <dd className="num text-right font-mono font-semibold text-text">{usd(total)}</dd>
        <dt className="text-muted">If Nemotron Ultra judged everything</dt>
        <dd className="num text-right font-mono text-muted">{usd(counterfactualUsd)}</dd>
      </dl>
      <div className="mt-2 block h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
        <span className="block h-full rounded-full bg-ultra opacity-40" style={{ width: `${(counterfactualUsd / max) * 100}%` }} />
      </div>
      {saved ? (
        <p className="mt-3 rounded-sm border tint-pass px-3 py-2 text-meta font-medium" data-testid="cost-saved">
          {saved}
        </p>
      ) : null}
      {replayTiers.length > 0 ? (
        <dl className="mt-3 grid grid-cols-[1fr_auto] gap-y-1 border-t border-border pt-3 text-meta" data-testid="replay-cost">
          <dt className="text-muted">Agent replays (same model as the original runs)</dt>
          <dd className="num text-right font-mono text-muted">
            {replayTiers.map((t) => `${usd(replayUsdByTier?.[t] ?? 0)} (${t})`).join(' + ')}
          </dd>
        </dl>
      ) : null}
    </section>
  )
}
