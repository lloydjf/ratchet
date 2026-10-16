import { memo, type MouseEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import type { PairVerdict } from '../api/types'
import { CONTROL, EMPTY, LEVEL0_LABEL } from '../copy'
import { truncate, usd } from '../lib/format'
import { useIsMobile } from '../lib/useMediaQuery'
import { changedArgsCopy, level0Copy, level0Kind, pairCostTiers, pairKey, sortPairs } from '../lib/pairs'
import { Badge } from './Badge'
import { OutcomeChip } from './OutcomeChip'
import { EmptyState } from './EmptyState'
import { TrailGlyph } from './TrailGlyph'

interface PairGridProps {
  pairs: PairVerdict[]
  verdictId: string
  /** `outcome` (default): problems first (see `lib/pairs`). `none`: keep the given order. */
  sort?: 'outcome' | 'none'
  loading?: boolean
  onOpen?: (p: PairVerdict) => void
  forceLayout?: 'table' | 'cards'
  /** The row the j/k keys have selected (`pairKey`). */
  selected?: string | null
  /** Show at most this many rows (the landing hero). */
  limit?: number
}

export const pairHref = (verdictId: string, p: Pick<PairVerdict, 'golden_run_id' | 'replay_run_id'>) =>
  `/verdicts/${encodeURIComponent(verdictId)}/pairs/${encodeURIComponent(p.golden_run_id)}/${encodeURIComponent(p.replay_run_id)}`

export const pairRowId = (p: Pick<PairVerdict, 'golden_run_id' | 'replay_run_id'>) => `pair-${pairKey(p).replace(/[^a-zA-Z0-9_-]/g, '-')}`

function Level0Marker({ p }: { p: PairVerdict }) {
  const kind = level0Kind(p)
  if (kind === 'hard') return <Badge text={LEVEL0_LABEL.hard} tone="neutral" hint={level0Copy(p)} />
  if (kind === 'changed') return <Badge text={LEVEL0_LABEL.changed} tone="neutral" hint={changedArgsCopy(p) ?? 'Tool calls differ from the original run.'} />
  return null
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
        <span className="text-meta font-medium text-warn-ink" data-testid="noisy-task">
          {CONTROL.noisy}
        </span>
      ) : null}
    </span>
  )
}

function ReasonLine({ p }: { p: PairVerdict }) {
  const l0 = level0Copy(p)
  return (
    <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1">
      <Level0Marker p={p} />
      <ControlMarker p={p} />
      {l0 ? <p className="text-meta text-muted">{l0}</p> : <p className="text-meta text-muted">{p.outcome === 'pass' && !isNoisy(p) ? p.task_label : p.reason}</p>}
    </div>
  )
}

function Cost({ p }: { p: PairVerdict }) {
  return (
    <span className="num whitespace-nowrap font-mono text-mono text-text">
      {usd(p.usd)} <span className="font-sans text-meta text-muted">({pairCostTiers(p)})</span>
    </span>
  )
}

/** Skeleton rows in the grid's own shape: chip, task line, trail, cost. */
const SkeletonRows = () => (
  <div className="flex flex-col" aria-busy="true" aria-label="Loading pairs" role="status">
    {Array.from({ length: 6 }, (_, i) => (
      <div key={i} className="grid grid-cols-[6rem_1fr_5.5rem_7rem] items-center gap-4 border-t border-border px-6 py-3.5 first:border-t-0 max-md:grid-cols-[5rem_1fr] max-md:px-4">
        <span className="skeleton h-5 w-20" />
        <span className="flex flex-col gap-1.5">
          <span className="skeleton block h-3.5 w-4/5" />
          <span className="skeleton block h-3 w-1/2" />
        </span>
        <span className="skeleton h-4 w-[81px] max-md:hidden" />
        <span className="skeleton h-3.5 w-24 max-md:hidden" />
      </div>
    ))}
  </div>
)

/** Failed pairs may have no replay trace (empty replay_run_id): nothing to open. */
export const canOpen = (p: PairVerdict) => !!p.golden_run_id && !!p.replay_run_id

const isInteractive = (el: EventTarget | null) => el instanceof Element && !!el.closest('a,button,[role="tooltip"]')

/** One hairline row per golden trace: outcome, task and Level 0 line, judge-trail glyph, cost, Open. */
export const PairGrid = memo(function PairGrid({ pairs, verdictId, sort = 'outcome', loading = false, onOpen, forceLayout, selected, limit }: PairGridProps) {
  const mobile = useIsMobile()
  const navigate = useNavigate()
  if (loading) return <SkeletonRows />
  if (!pairs.length) return <EmptyState title={EMPTY.pairs} />
  const sorted = sort === 'outcome' ? sortPairs(pairs) : pairs
  const rows = limit ? sorted.slice(0, limit) : sorted
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
      <ul className="flex flex-col" data-testid="pair-grid" data-layout="cards">
        {rows.map((p) => (
          <li key={pairKey(p)} id={pairRowId(p)} data-outcome={p.outcome} data-selected={selected === pairKey(p) || undefined} className="relative border-t border-border px-4 py-3 first:border-t-0 data-[selected]:bg-surface-2">
            <div className="flex items-start gap-3">
              <PairOutcome p={p} size="sm" className="mt-0.5" />
              <div className="min-w-0 flex-1">
                {canOpen(p) ? (
                  <Link to={pairHref(verdictId, p)} onClick={() => onOpen?.(p)} className="block text-body font-medium text-text after:absolute after:inset-0 after:content-['']" title={p.task_input}>
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
            <div className="relative z-10 mt-2 flex flex-wrap items-center justify-between gap-2">
              <TrailGlyph p={p} />
              <Cost p={p} />
            </div>
          </li>
        ))}
      </ul>
    )

  return (
    <div data-testid="pair-grid" data-layout="table">
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">Replayed tasks, worst outcome first</caption>
        <thead>
          <tr className="text-meta text-muted">
            <th scope="col" className="w-[9rem] py-2 pr-2 pl-6 font-normal">
              Outcome
            </th>
            <th scope="col" className="px-2 py-2 font-normal">
              Task and Level 0 check
            </th>
            <th scope="col" className="w-[7.5rem] px-2 py-2 font-normal">
              Judges
            </th>
            <th scope="col" className="w-[10rem] px-2 py-2 text-right font-normal">
              Cost
            </th>
            <th scope="col" className="w-[5.5rem] py-2 pr-6 pl-2">
              <span className="sr-only">Open</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr
              key={pairKey(p)}
              id={pairRowId(p)}
              data-outcome={p.outcome}
              data-selected={selected === pairKey(p) || undefined}
              onClick={rowClick(p)}
              className={`border-t border-border align-top transition-colors duration-[160ms] data-[selected]:bg-surface-2 ${canOpen(p) ? 'cursor-pointer hover:bg-surface-2' : ''}`}
            >
              <td className="py-3 pr-2 pl-6">
                <PairOutcome p={p} size="sm" />
              </td>
              <td className="max-w-0 px-2 py-3">
                <p className="truncate text-body text-text" title={p.task_input}>
                  {truncate(p.task_input, 80)}
                </p>
                <ReasonLine p={p} />
              </td>
              <td className="px-2 py-2">
                <TrailGlyph p={p} />
              </td>
              <td className="px-2 py-3 text-right">
                <Cost p={p} />
              </td>
              <td className="py-1.5 pr-6 pl-2 text-right">
                {canOpen(p) ? (
                  <Link to={pairHref(verdictId, p)} onClick={() => onOpen?.(p)} className="link inline-flex min-h-8 items-center gap-1 text-body" aria-label={`Open ${truncate(p.task_label || p.task_input, 60)}`}>
                    Open <ArrowRight size={14} aria-hidden="true" />
                  </Link>
                ) : (
                  <span className="inline-flex min-h-8 items-center whitespace-nowrap text-meta text-muted">No replay</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
})
