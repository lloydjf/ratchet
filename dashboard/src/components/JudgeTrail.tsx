import type { ReactNode } from 'react'
import type { DecidedBy, DriftCheck, JudgeOpinion, Tier } from '../api/types'
import { TIERS } from '../api/types'
import { CONTROL } from '../copy'
import { TierBadge } from './TierBadge'
import { Tooltip } from './Tooltip'

interface JudgeTrailProps {
  opinions: JudgeOpinion[]
  decidedBy?: DecidedBy
  drift?: DriftCheck[]
  size?: 'sm' | 'md'
}

const Arrow = () => (
  <span aria-hidden="true" className="px-0.5 font-mono text-mono text-muted">
    →
  </span>
)

/** Sort opinions into cascade order (nano → super → ultra). */
export function cascadeOrder(opinions: JudgeOpinion[]): JudgeOpinion[] {
  return [...opinions].sort((a, b) => TIERS.indexOf(a.tier) - TIERS.indexOf(b.tier))
}

/**
 * Tier badges in cascade order joined by arrows. Tiers not consulted render dotted
 * ("Not needed: decided earlier in the cascade."). Level 0 decisions open with an L0
 * marker; Tavily drift checks trail with a T marker; a pair a control replay held
 * (`decided_by: "control"`, ADR 0023) ends with a C marker.
 */
export function JudgeTrail({ opinions, decidedBy, drift = [], size = 'md' }: JudgeTrailProps) {
  const byTier = new Map<Tier, JudgeOpinion>()
  for (const o of cascadeOrder(opinions)) if (!byTier.has(o.tier)) byTier.set(o.tier, o)
  const level0 = decidedBy === 'level0'
  const items: ReactNode[] = []
  const dim = size === 'sm' ? 'h-6 text-mono' : 'h-7 text-meta'
  if (level0) {
    items.push(
      <Tooltip key="l0" content="Level 0: checked without a model.">
        {(t) => (
          <button type="button" className={`inline-flex items-center rounded-sm border px-1.5 font-mono font-bold tint-block ${dim} cursor-help`} {...t}>
            <span aria-hidden="true">L0</span>
            <span className="sr-only">Level 0: checked without a model</span>
          </button>
        )}
      </Tooltip>,
    )
  }
  TIERS.forEach((tier) => {
    const o = byTier.get(tier)
    if (items.length) items.push(<Arrow key={`a-${tier}`} />)
    items.push(
      o ? (
        <TierBadge key={tier} tier={tier} verdict={o.verdict} confidence={o.confidence} reasons={o.reasons} size={size} />
      ) : (
        <TierBadge key={tier} tier={tier} skipped size={size} />
      ),
    )
  })
  if (drift.length) {
    const changed = drift.some((d) => d.changed && d.status === 'checked')
    const unavailable = drift.every((d) => d.status === 'unavailable')
    const tip = unavailable
      ? 'Tavily: could not verify whether the world changed.'
      : changed
        ? 'Tavily: a fact this task depends on changed.'
        : 'Tavily: the facts this task depends on did not change.'
    items.push(<Arrow key="a-t" />)
    items.push(
      <Tooltip key="t" content={tip}>
        {(t) => (
          <button type="button" className={`inline-flex items-center rounded-sm border px-1.5 font-mono font-bold ${unavailable ? 'tint-warn' : 'tint-drift'} ${dim} cursor-help`} {...t}>
            <span aria-hidden="true">T</span>
            <span className="sr-only">{tip}</span>
          </button>
        )}
      </Tooltip>,
    )
  }
  if (decidedBy === 'control') {
    items.push(<Arrow key="a-c" />)
    items.push(
      <Tooltip key="c" content={CONTROL.trailTip}>
        {(t) => (
          <button type="button" className={`inline-flex items-center rounded-sm border px-1.5 font-mono font-bold tint-warn ${dim} cursor-help`} data-testid="trail-control" {...t}>
            <span aria-hidden="true">C</span>
            <span className="sr-only">{CONTROL.trailTip}</span>
          </button>
        )}
      </Tooltip>,
    )
  }
  return (
    <span className="inline-flex flex-nowrap items-center gap-0.5" data-testid="judge-trail" role="group" aria-label="Judge trail">
      {items}
    </span>
  )
}
