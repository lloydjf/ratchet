import { forwardRef, memo } from 'react'
import { Bot, Wrench } from 'lucide-react'
import type { SideEffectClass, Step, Tier } from '../api/types'
import { HARD_SIDE_EFFECTS } from '../api/types'
import { BADGE, EVIDENCE, TIER_LETTER, TIER_NAME, TIER_SHORT } from '../copy'
import { usdTier } from '../lib/format'
import { TierBadge } from './TierBadge'

export interface StepRow {
  step: Step | null
  differs: boolean
}

interface StepListProps {
  rows: StepRow[]
  side: 'golden' | 'replay'
  highlightDiffs?: boolean
  /** Step id to flash (from an opinion's cited step). */
  focusStep?: string | null
  differencesOnly?: boolean
  /** Bumped on every jump to `focusStep`, so the flash plays once per jump. */
  flash?: number
  /** Judges that cited each step (the link back to their opinion). */
  citedBy?: Map<string, Tier[]>
  onScroll?: () => void
  className?: string
  maxHeight?: string
}

function SideEffectChip({ cls }: { cls: SideEffectClass }) {
  const hard = HARD_SIDE_EFFECTS.has(cls)
  const tone = hard ? 'tint-block' : cls === 'read' || cls === 'none' ? 'tint-neutral' : 'tint-warn'
  return <span className={`inline-flex h-5 items-center border px-1.5 font-mono text-mono ${tone}`}>{cls}</span>
}

const StepCard = memo(function StepCard({ step, differs, highlight, focused, flash, cited }: { step: Step; differs: boolean; highlight: boolean; focused: boolean; flash: boolean; cited?: Tier[] }) {
  const hl = highlight && differs
  return (
    <div
      id={`step-${step.step_id}`}
      tabIndex={-1}
      className={`scroll-mt-24 border p-3 transition-colors duration-[160ms] ${flash ? 'anim-flash' : ''} ${focused ? 'border-accent bg-surface-2' : hl ? 'border-[color-mix(in_srgb,var(--warn)_55%,var(--border))] bg-[var(--highlight)]' : 'border-border bg-surface'}`}
      data-step={step.step_id}
      data-differs={differs || undefined}
      data-focused={focused || undefined}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-mono font-semibold text-muted">{step.step_id}</span>
        {step.kind === 'llm' ? (
          <>
            <Bot size={16} aria-hidden="true" className="text-muted" />
            <TierBadge tier={step.tier} plain size="sm" />
            <span className="text-meta text-text">Model call · {TIER_SHORT[step.tier]}</span>
          </>
        ) : (
          <>
            <Wrench size={16} aria-hidden="true" className="text-muted" />
            <span className="font-mono text-meta font-medium text-text">{step.tool}</span>
            <SideEffectChip cls={step.side_effect} />
            {step.blocked ? <span className="inline-flex h-6 items-center rounded-sm border px-2 text-mono font-semibold tint-block">{BADGE.blocked}</span> : null}
          </>
        )}
        {cited?.length ? (
          <span className="flex items-center gap-1.5 text-meta text-muted">
            {EVIDENCE.citedBy}
            {cited.map((t) => (
              <a key={t} href={`#opinion-${t}`} className="link inline-flex h-6 min-w-6 items-center justify-center rounded-sm font-mono" aria-label={`${EVIDENCE.citedBy} ${TIER_NAME[t]}`}>
                {TIER_LETTER[t]}
              </a>
            ))}
          </span>
        ) : null}
        {hl ? (
          <span className="ml-auto text-mono font-medium text-warn-ink">
            <span aria-hidden="true">≠ </span>Differs
          </span>
        ) : null}
      </div>
      {step.kind === 'tool' ? (
        <>
          <details className="group mt-2">
            <summary className="inline-flex min-h-8 cursor-pointer items-center text-mono text-muted select-none hover:text-text">
              Arguments
            </summary>
            <pre className="mt-1 overflow-x-auto rounded-sm bg-surface-2 p-2 font-mono text-mono whitespace-pre-wrap break-words text-text">{JSON.stringify(step.args_norm, null, 2)}</pre>
          </details>
          {step.result_preview ? (
            <div className="mt-1">
              <p className="text-mono text-muted">{step.blocked ? 'Refused in the sandbox' : 'Result'}</p>
              <p className={`mt-0.5 line-clamp-4 font-mono text-mono break-words ${step.blocked ? 'text-block-ink' : 'text-text/80'}`}>{step.result_preview.slice(0, 400)}</p>
            </div>
          ) : null}
        </>
      ) : (
        <>
          <p className="mt-2 line-clamp-5 text-meta whitespace-pre-line text-text">{step.content_preview.slice(0, 400)}</p>
          <p className="num mt-1 font-mono text-mono text-muted">
            {usdTier(step.usd, step.tier)} · {step.latency_ms} ms
          </p>
        </>
      )}
    </div>
  )
})

/** One run's steps as a vertical list; gaps mark steps only the other run has. */
export const StepList = forwardRef<HTMLDivElement, StepListProps>(function StepList(
  { rows, side, highlightDiffs = true, focusStep, differencesOnly = false, onScroll, className = '', maxHeight, flash = 0, citedBy },
  ref,
) {
  return (
    <div
      ref={ref}
      onScroll={onScroll}
      className={`overflow-y-auto overscroll-contain ${className}`}
      style={maxHeight ? { maxHeight } : undefined}
      data-testid={`steps-${side}`}
      tabIndex={0}
      role="region"
      aria-label={side === 'golden' ? 'Golden run steps' : 'Replay steps'}
    >
      <ol className="flex flex-col gap-2 p-2">
        {rows.map((r, i) => {
          if (differencesOnly && !r.differs) return null
          return (
            <li key={i} data-row={i}>
              {r.step ? (
                <StepCard
                  key={!!focusStep && r.step.step_id === focusStep ? `f${flash}` : 'n'}
                  step={r.step}
                  differs={r.differs}
                  highlight={highlightDiffs}
                  focused={!!focusStep && r.step.step_id === focusStep}
                  flash={flash > 0 && !!focusStep && r.step.step_id === focusStep}
                  cited={citedBy?.get(r.step.step_id)}
                />
              ) : (
                <div className="flex min-h-16 items-center justify-center border border-dashed border-border-strong px-3 py-4 text-center text-mono text-muted">
                  {side === 'golden' ? 'Not in the original run' : 'Not in the replay'}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
})
