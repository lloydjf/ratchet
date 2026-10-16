import { ExternalLink, Globe } from 'lucide-react'
import type { DriftCheck } from '../api/types'
import { BADGE, errorCopy } from '../copy'
import { pct } from '../lib/format'
import { ActionButton } from './ActionButton'
import { Badge } from './Badge'

export type DriftState = 'pending' | 'changed' | 'unchanged' | 'unavailable'

export function driftState(check: DriftCheck | null | undefined): DriftState {
  if (!check) return 'pending'
  if (check.status === 'unavailable') return 'unavailable'
  return check.changed ? 'changed' : 'unchanged'
}

interface DriftCardProps {
  check: DriftCheck | null
  /** Task label for the Re-bless confirmation. */
  task?: string
  onRebless?: () => Promise<unknown>
  reblessed?: boolean
  onSourceClick?: () => void
  onReblessClick?: () => void
  className?: string
}

function host(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

const HEAD: Record<DriftState, { label: string; tone: string }> = {
  pending: { label: 'Asking Tavily', tone: 'text-muted' },
  changed: { label: 'World changed', tone: 'text-drift-ink' },
  unchanged: { label: 'World unchanged', tone: 'text-text' },
  unavailable: { label: 'Could not check', tone: 'text-warn-ink' },
}

/** A Tavily drift check: fact, old → new value, source link, ruling, and Re-bless. */
export function DriftCard({ check, task, onRebless, reblessed, onSourceClick, onReblessClick, className = '' }: DriftCardProps) {
  const state = driftState(check)
  const head = HEAD[state]
  return (
    <article
      className={`card relative overflow-hidden p-4 ${state === 'changed' ? 'border-[color-mix(in_srgb,var(--drift)_45%,var(--border))]' : ''} ${className}`}
      data-testid="drift-card"
      data-state={state}
    >
      <span aria-hidden="true" className="absolute inset-y-0 left-0 w-[3px]" style={{ background: state === 'changed' ? 'var(--drift)' : state === 'unavailable' ? 'var(--warn)' : 'var(--border)' }} />
      <header className="flex flex-wrap items-center gap-2">
        <Globe size={16} aria-hidden="true" className={head.tone} />
        <h3 className={`text-meta font-semibold uppercase tracking-[0.06em] ${head.tone}`}>{head.label}</h3>
        {state === 'unavailable' ? <Badge text={BADGE.driftUnavailable} tone="warn" /> : null}
        {check && state !== 'unavailable' ? <span className="num ml-auto font-mono text-mono text-muted">Tavily · {pct(check.confidence)} confident</span> : null}
      </header>

      {state === 'pending' ? (
        <div className="mt-3 flex flex-col gap-2" aria-busy="true">
          <span className="skeleton h-4 w-3/4" />
          <span className="skeleton h-8 w-1/2" />
        </div>
      ) : (
        <>
          <p className="mt-3 text-body text-text">{check!.statement || check!.query}</p>
          {state !== 'unavailable' ? (
            <p className="num mt-3 flex flex-wrap items-baseline gap-x-3 gap-y-1 font-mono" aria-label={`Old value ${check!.old_value}, new value ${check!.new_value ?? 'unknown'}`}>
              <span className={`text-h2 ${state === 'changed' ? 'text-muted line-through decoration-1' : 'text-text'}`}>{check!.old_value}</span>
              <span aria-hidden="true" className="text-h3 text-muted">
                →
              </span>
              <span className={`text-h2 font-semibold ${state === 'changed' ? 'text-drift-ink' : 'text-text'}`}>{check!.new_value ?? '—'}</span>
            </p>
          ) : (
            <p className="mt-2 text-meta text-warn-ink">{errorCopy('drift_unavailable')}</p>
          )}
          {check!.source_url ? (
            <a
              href={check!.source_url}
              target="_blank"
              rel="noreferrer noopener"
              onClick={onSourceClick}
              className="mt-3 inline-flex min-h-11 max-w-full items-center gap-2 rounded-sm border border-border bg-surface-2 px-3 text-meta text-text hover:border-border-strong"
            >
              <span className="shrink-0 text-muted">Source</span>
              <span className="min-w-0 truncate font-medium">{check!.source_title ?? host(check!.source_url)}</span>
              <span className="shrink-0 font-mono text-mono text-muted">{host(check!.source_url)}</span>
              <ExternalLink size={14} aria-hidden="true" className="shrink-0 text-muted" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          ) : null}
          {check!.ruling ? (
            <p className="mt-3 text-body text-text">
              <span className="font-semibold">Ruling: </span>
              {check!.ruling}
            </p>
          ) : null}
          {state === 'changed' && onRebless ? (
            <div className="mt-4">
              <ActionButton kind="rebless" subject={task ?? check!.statement} onConfirm={onRebless} done={reblessed} doneLabel="Re-blessed" size="sm" onClick={onReblessClick} />
            </div>
          ) : null}
        </>
      )}
    </article>
  )
}
