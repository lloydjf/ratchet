import type { TierMap } from '../api/types'
import { sumTiers, tiersWithSpend, usd } from '../lib/format'

/**
 * A dollar total with the tiers that incurred it, "$0.031 (nano, super)", tiers muted
 * (costs always with tier; never a bare dollar figure).
 */
export function UsdTiers({ map, none = 'no model', className = '' }: { map: TierMap<number> | null | undefined; none?: string; className?: string }) {
  return (
    <span className={className} data-testid="usd-tiers">
      <span className="font-mono">{usd(sumTiers(map))}</span> <span className="text-muted">({tiersWithSpend(map, none)})</span>
    </span>
  )
}
