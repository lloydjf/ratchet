import type { JudgeOpinion } from '../api/types'
import { BADGE, OPINION_GLYPH, OPINION_LABEL, TIER_NAME, TRUNCATED_MARKER } from '../copy'
import { pct, tokens, usdTier } from '../lib/format'
import { Badge } from './Badge'
import { TierBadge } from './TierBadge'

const VERDICT_INK = { equivalent: 'text-pass-ink', improved: 'text-pass-ink', regressed: 'text-block-ink', uncertain: 'text-warn-ink' } as const
const VERDICT_BAR = { equivalent: 'var(--pass)', improved: 'var(--pass)', regressed: 'var(--block)', uncertain: 'var(--warn)' } as const

interface OpinionCardProps {
  opinion: JudgeOpinion
  onStepClick?: (stepId: string) => void
  className?: string
}

/** One judge's opinion: tier, verdict, confidence bar, reasons, cited steps, truncation. */
export function OpinionCard({ opinion: o, onStepClick, className = '' }: OpinionCardProps) {
  return (
    <article className={`card flex flex-col gap-3 p-4 ${className}`} data-testid="opinion-card" data-tier={o.tier}>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <TierBadge tier={o.tier} plain />
        <h3 className="text-body font-semibold text-text">{TIER_NAME[o.tier]}</h3>
        <span className={`inline-flex items-center gap-1.5 text-meta font-semibold ${VERDICT_INK[o.verdict]}`}>
          <span aria-hidden="true" className="font-mono">
            {OPINION_GLYPH[o.verdict]}
          </span>
          {OPINION_LABEL[o.verdict]}
        </span>
        {o.truncated ? <Badge text={BADGE.truncated} tone="warn" hint={TRUNCATED_MARKER} /> : null}
        <span className="num ml-auto font-mono text-mono text-muted">
          {usdTier(o.usd, o.tier)} · {tokens(o.prompt_tokens + o.completion_tokens)} tok
        </span>
      </header>
      <div className="flex items-center gap-3">
        <span className="text-mono text-muted">Confidence</span>
        <span
          className="block h-1.5 flex-1 overflow-hidden rounded-full bg-surface-2"
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(o.confidence * 100)}
          aria-label={`Confidence ${pct(o.confidence)}`}
        >
          <span className="block h-full rounded-full" style={{ width: pct(o.confidence), background: VERDICT_BAR[o.verdict] }} />
        </span>
        <span className="num w-10 text-right font-mono text-meta font-semibold text-text">{pct(o.confidence)}</span>
      </div>
      {o.reasons.length ? (
        <ul className="flex flex-col gap-1.5 text-body text-text">
          {o.reasons.map((r, i) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden="true" className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-muted" />
              <span>{r}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {o.causal_explanation ? <p className="text-meta text-muted">Cause: {o.causal_explanation}</p> : null}
      {o.truncated ? <p className="text-meta text-warn-ink">{TRUNCATED_MARKER}</p> : null}
      {o.criteria_met.length ? (
        <ul className="flex flex-col gap-1 border-t border-border pt-3 text-meta">
          {o.criteria_met.map((c, i) => (
            <li key={i} className="flex gap-2">
              <span aria-hidden="true" className={`font-mono font-bold ${c.met ? 'text-pass-ink' : 'text-block-ink'}`}>
                {c.met ? '✓' : '✕'}
              </span>
              <span className="sr-only">{c.met ? 'Met:' : 'Not met:'}</span>
              <span className="text-text">{c.criterion}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {o.evidence_steps.length ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
          <span className="text-mono text-muted">Cited steps</span>
          {o.evidence_steps.map((s) =>
            onStepClick ? (
              <button
                key={s}
                type="button"
                onClick={() => onStepClick(s)}
                className="inline-flex min-h-8 items-center rounded-sm border border-border-strong bg-bg px-2 font-mono text-mono text-accent hover:bg-surface-2"
                aria-label={`Show step ${s}`}
              >
                {s}
              </button>
            ) : (
              <span key={s} className="rounded-sm border border-border bg-surface-2 px-2 py-0.5 font-mono text-mono text-text">
                {s}
              </span>
            ),
          )}
        </div>
      ) : null}
    </article>
  )
}
