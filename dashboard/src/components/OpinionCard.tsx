import type { JudgeOpinion } from '../api/types'
import { BADGE, OPINION_LABEL, TIER_NAME, TRUNCATED_MARKER } from '../copy'
import { pct, tokens, usdTier } from '../lib/format'
import { Badge } from './Badge'
import { OpinionMark } from './Marks'
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
    <article id={`opinion-${o.tier}`} tabIndex={-1} className={`card flex scroll-mt-24 flex-col gap-3 p-4 ${className}`} data-testid="opinion-card" data-tier={o.tier}>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <TierBadge tier={o.tier} plain />
        <h3 className="text-body font-semibold text-text">{TIER_NAME[o.tier]}</h3>
        <span className={`inline-flex items-center gap-1.5 text-meta font-semibold ${VERDICT_INK[o.verdict]}`}>
          <OpinionMark verdict={o.verdict} />
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
          className="block h-[3px] flex-1 bg-border"
          role="meter"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(o.confidence * 100)}
          aria-label={`Confidence ${pct(o.confidence)}`}
        >
          <span className="block h-full" style={{ width: pct(o.confidence), background: VERDICT_BAR[o.verdict] }} />
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
              <OpinionMark verdict={c.met ? 'equivalent' : 'regressed'} className={`mt-0.5 ${c.met ? 'text-pass-ink' : 'text-block-ink'}`} />
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
              <a
                key={s}
                href={`#step-${s}`}
                onClick={(e) => {
                  e.preventDefault()
                  onStepClick(s)
                }}
                className="link inline-flex min-h-7 items-center rounded-sm px-1 font-mono text-mono underline"
                aria-label={`Show step ${s}`}
              >
                {s}
              </a>
            ) : (
              <span key={s} className="border border-border bg-surface-2 px-1.5 py-0.5 font-mono text-mono text-text">
                {s}
              </span>
            ),
          )}
        </div>
      ) : null}
    </article>
  )
}
