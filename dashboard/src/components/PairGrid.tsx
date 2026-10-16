import { memo, type MouseEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { PairVerdict } from '../api/types'
import { CONTROL, EMPTY, LEVEL0_LABEL } from '../copy'
import { truncate, usd } from '../lib/format'
import { useIsMobile } from '../lib/useMediaQuery'
import { changedArgsCopy, level0Copy, level0Kind, pairCostTiers, pairKey, sortPairs } from '../lib/pairs'
import { Badge } from './Badge'
import { JudgeTrail } from './JudgeTrail'
import { OutcomeChip } from './OutcomeChip'
import { EmptyState } from './EmptyState'

interface PairGridProps {
  pairs: PairVerdict[]
  verdictId: string
  /** `outcome` (default): problems first (see `lib/pairs`). `none`: keep the given order. */
  sort?: 'outcome' | 'none'
  loading?: boolean
  onOpen?: (p: PairVerdict) => void
  forceLayout?: 'table' | 'cards'
}

export const pairHref = (verdictId: string, p: PairVerdict) =>
  `/verdicts/${encodeURIComponent(verdictId)}/pairs/${encodeURIComponent(p.golden_run_id)}/${encodeURIComponent(p.replay_run_id)}`

function Level0Marker({ p }: { p: PairVerdict }) {
  const kind = level0Kind(p)
  if (kind === 'hard') return <Badge text={LEVEL0_LABEL.hard} tone="block" hint={level0Copy(p)} />
  if (kind === 'changed') return <Badge text={LEVEL0_LABEL.changed} tone="neutral" hint={changedArgsCopy(p) ?? 'Tool calls differ from the original run.'} />
  return (
    <span className="text-meta text-muted">
      <span aria-hidden="true">—</span>
      <span className="sr-only">{LEVEL0_LABEL.none}</span>
    </span>
  )
}

/** ADR 0023: what the control replay (the unchanged skill) showed, on pairs that had one. */
export function ControlMarker({ p }: { p: Pick<PairVerdict, 'control_outcome'> }) {
  if (p.control_outcome === 'agrees') return <Badge text={CONTROL.agrees} tone="neutral" hint={CONTROL.agreesHint} />
  if (p.control_outcome === 'diverges') return <Badge text={CONTROL.diverges} tone="warn" hint={CONTROL.divergesHint} />
  return null
}

/** A diverging control decided this pair: the difference is the model's noise, not the edit. */
export const isNoisy = (p: Pick<PairVerdict, 'decided_by' | 'control_outcome'>) => p.decided_by === 'control' && p.control_outcome === 'diverges'

/** Outcome chip plus the visible "(noisy task)" qualifier on control-decided pairs. */
export function PairOutcome({ p, size, className = '' }: { p: PairVerdict; size?: 'sm' | 'md'; className?: string }) {
  return (
    <span className={`inline-flex flex-wrap items-center gap-1.5 ${className}`}>
      <OutcomeChip outcome={p.outcome} size={size} />
      {isNoisy(p) ? (
        <span className="text-mono font-medium text-warn-ink" data-testid="noisy-task">
          {CONTROL.noisy}
        </span>
      ) : null}
    </span>
  )
}

function ReasonLine({ p }: { p: PairVerdict }) {
  const kind = level0Kind(p)
  const l0 = level0Copy(p)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
      {kind !== 'none' ? <Level0Marker p={p} /> : null}
      <ControlMarker p={p} />
      {l0 ? (
        <p className="text-meta font-medium text-block-ink">{l0}</p>
      ) : (
        <p className="text-meta text-muted">{p.outcome === 'pass' && !isNoisy(p) ? p.task_label : p.reason}</p>
      )}
    </div>
  )
}

function Cost({ p }: { p: PairVerdict }) {
  return (
    <span className="num whitespace-nowrap font-mono text-mono text-text">
      {usd(p.usd)} <span className="text-muted">({pairCostTiers(p)})</span>
    </span>
  )
}

const SkeletonRows = () => (
  <div className="flex flex-col gap-2" aria-busy="true" aria-label="Loading pairs">
    {Array.from({ length: 6 }, (_, i) => (
      <div key={i} className="flex h-14 items-center gap-4 rounded-sm border border-border bg-surface px-4">
        <span className="skeleton h-6 w-24" />
        <span className="skeleton h-4 flex-1" />
        <span className="skeleton h-6 w-28" />
      </div>
    ))}
  </div>
)

/** Failed pairs may have no replay trace (empty replay_run_id): nothing to open. */
export const canOpen = (p: PairVerdict) => !!p.golden_run_id && !!p.replay_run_id

const isInteractive = (el: EventTarget | null) => el instanceof Element && !!el.closest('a,button,[role="tooltip"]')

/** One row per golden trace: outcome, task, Level 0 marker, judge trail, cost, Open. */
export const PairGrid = memo(function PairGrid({ pairs, verdictId, sort = 'outcome', loading = false, onOpen, forceLayout }: PairGridProps) {
  const mobile = useIsMobile()
  const navigate = useNavigate()
  if (loading) return <SkeletonRows />
  if (!pairs.length) return <EmptyState title={EMPTY.pairs} />
  const rows = sort === 'outcome' ? sortPairs(pairs) : pairs
  const layout = forceLayout ?? (mobile ? 'cards' : 'table')

  const open = (p: PairVerdict) => {
    onOpen?.(p)
    navigate(pairHref(verdictId, p))
  }
  const rowClick = (p: PairVerdict) => (e: MouseEvent) => {
    if (isInteractive(e.target) || !canOpen(p)) return
    open(p)
  }

  if (layout === 'cards')
    return (
      <ul className="flex flex-col gap-2" data-testid="pair-grid" data-layout="cards">
        {rows.map((p) => (
          <li key={pairKey(p)} data-outcome={p.outcome} className="relative rounded-card border border-border bg-surface p-3 hover:border-border-strong">
            <div className="flex items-start gap-3">
              <PairOutcome p={p} size="sm" className="mt-0.5" />
              <div className="min-w-0 flex-1">
                {canOpen(p) ? (
                  <Link
                    to={pairHref(verdictId, p)}
                    onClick={() => onOpen?.(p)}
                    className="block text-body font-medium text-text after:absolute after:inset-0 after:rounded-card after:content-['']"
                    title={p.task_input}
                  >
                    {truncate(p.task_input, 80)}
                  </Link>
                ) : (
                  <p className="text-body font-medium text-text" title={p.task_input}>
                    {truncate(p.task_input, 80)}
                  </p>
                )}
                <ReasonLine p={p} />
              </div>
            </div>
            <div className="relative z-10 mt-3 flex flex-wrap items-center justify-between gap-2 pl-0">
              <JudgeTrail opinions={p.opinions} decidedBy={p.decided_by} drift={p.drift} size="sm" />
              <Cost p={p} />
            </div>
          </li>
        ))}
      </ul>
    )

  return (
    <div className="overflow-hidden rounded-card border border-border" data-testid="pair-grid" data-layout="table">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Replayed tasks, worst outcome first</caption>
        <thead className="bg-surface">
          <tr className="text-mono uppercase tracking-[0.06em] text-muted">
            <th scope="col" className="w-[8.75rem] py-2.5 pr-2 pl-4 font-medium">Outcome</th>
            <th scope="col" className="px-2 py-2.5 font-medium">Task and Level 0 check</th>
            <th scope="col" className="w-[12.5rem] px-2 py-2.5 font-medium">Judges and cost</th>
            <th scope="col" className="w-[4.75rem] py-2.5 pr-4 pl-2">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={pairKey(p)}
              data-outcome={p.outcome}
              onClick={rowClick(p)}
              className={`border-t border-border bg-bg align-top transition-colors duration-[160ms] ${canOpen(p) ? 'cursor-pointer hover:bg-surface' : ''}`}
            >
              <td className="py-3 pr-2 pl-4">
                <PairOutcome p={p} size="sm" />
              </td>
              <td className="max-w-0 px-2 py-3">
                <p className="truncate text-body text-text" title={p.task_input}>
                  {truncate(p.task_input, 80)}
                </p>
                <ReasonLine p={p} />
              </td>
              <td className="px-2 py-3">
                <JudgeTrail opinions={p.opinions} decidedBy={p.decided_by} drift={p.drift} size="sm" />
                <div className="mt-1">
                  <Cost p={p} />
                </div>
              </td>
              <td className="py-1.5 pr-4 pl-2 text-right">
                {canOpen(p) ? (
                  <Link
                    to={pairHref(verdictId, p)}
                    onClick={() => onOpen?.(p)}
                    className="link inline-flex min-h-11 items-center gap-1 text-meta font-medium"
                    aria-label={`Open ${truncate(p.task_label || p.task_input, 60)}`}
                  >
                    Open <ArrowRight size={14} aria-hidden="true" />
                  </Link>
                ) : (
                  <span className="inline-flex min-h-11 items-center whitespace-nowrap text-mono text-muted">No replay</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})
