import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { PairVerdict, Verdict, VerdictView } from '../../api/types'
import { track } from '../../analytics'
import { EVIDENCE, firstSentence, OUTCOME_LABEL, TIER_NAME, undoHint, VERDICT } from '../../copy'
import { ActionButton } from '../../components/ActionButton'
import { DiffViewer } from '../../components/DiffViewer'
import { DriftCard } from '../../components/DriftCard'
import { PairGrid } from '../../components/PairGrid'
import { OUTCOME_ORDER, pairKey } from '../../lib/pairs'

export function Panel({ id, title, aside, children, className = '', flush = false }: { id: string; title: string; aside?: ReactNode; children: ReactNode; className?: string; flush?: boolean }) {
  return (
    <section aria-labelledby={id} className={`flex flex-col ${flush ? '' : 'gap-4 panel-pad'} ${className}`}>
      <div className={`flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 ${flush ? 'px-6 pt-5 pb-3 max-md:px-4' : ''}`}>
        <h2 id={id} className="text-body font-semibold text-text">
          {title}
        </h2>
        {aside}
      </div>
      {children}
    </section>
  )
}

function OutcomeSummary({ pairs }: { pairs: PairVerdict[] }) {
  const counts = new Map<string, number>()
  for (const p of pairs) counts.set(p.outcome, (counts.get(p.outcome) ?? 0) + 1)
  return (
    <p className="num text-meta text-muted">
      {OUTCOME_ORDER.filter((o) => counts.get(o))
        .map((o) => `${counts.get(o)} ${OUTCOME_LABEL[o].toLowerCase()}`)
        .join(' · ')}
    </p>
  )
}

/** The narrative minus its opening sentence, which is already the band's reason. */
export function useNarrativeBody(v: Verdict | null): string {
  return useMemo(() => {
    if (!v) return ''
    if (v.headline && v.narrative.startsWith(v.headline)) return v.narrative.slice(v.headline.length).trim()
    const first = firstSentence(v.narrative)
    return first === v.headline ? v.narrative.slice(first.length).trim() : v.narrative
  }, [v])
}

export function NarrativePanel({ v }: { v: Verdict }) {
  const body = useNarrativeBody(v)
  if (!body) return null
  return (
    <section aria-labelledby="summary-title" className="panel-pad">
      <h2 id="summary-title" className="mb-3 text-body font-semibold text-text">
        {VERDICT.summary}
      </h2>
      <p className="max-w-prose text-narrative text-text" data-testid="narrative">
        {body}
      </p>
      <p className="mt-2 text-meta text-muted">{VERDICT.writtenFromAbove(TIER_NAME.ultra)}</p>
    </section>
  )
}

interface PairsPanelProps {
  view: VerdictView
  verdictId: string
  /** Pair keys the active hunk filter keeps (null: no filter). */
  only: Set<string> | null
  onClearFilter: () => void
  selected: string | null
  limit?: number
}

export function PairsPanel({ view, verdictId, only, onClearFilter, selected, limit }: PairsPanelProps) {
  const v = view.verdict
  const pairs = v ? (only ? v.pairs.filter((p) => only.has(pairKey(p))) : v.pairs) : []
  return (
    <Panel id="pairs-title" flush title={v ? `${VERDICT.replayed} (${v.pairs.length})` : VERDICT.replayed} aside={v ? <OutcomeSummary pairs={v.pairs} /> : null}>
      {only && v ? (
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border bg-surface-2 px-6 py-2 text-meta text-text max-md:px-4" role="status" data-testid="hunk-filter">
          {EVIDENCE.filtered(pairs.length, v.pairs.length)}
          <button type="button" onClick={onClearFilter} className="link min-h-8 text-meta">
            {EVIDENCE.clearFilter}
          </button>
        </p>
      ) : null}
      <div className="border-t border-border">
        {v ? (
          <PairGrid pairs={pairs} verdictId={v.verdict_id} selected={selected} limit={limit} onOpen={() => track('demo.evidence_open', { which: 'pair' })} />
        ) : (
          <PairGrid pairs={[]} verdictId={verdictId} loading />
        )}
      </div>
    </Panel>
  )
}

interface DiffPanelProps {
  view: VerdictView
  mobile: boolean
  hunkCounts?: Map<string, number>
  activeHunk: string | null
  onHunk: (id: string | null) => void
}

export function DiffPanel({ view, mobile, hunkCounts, activeHunk, onHunk }: DiffPanelProps) {
  return (
    <Panel
      id="diff-title"
      title={VERDICT.diff}
      aside={
        <Link to={`/edits/${encodeURIComponent(view.edit.edit_id)}`} className="link text-meta">
          {VERDICT.editPage}
        </Link>
      }
    >
      {view.edit.summary ? (
        <p className="-mt-1 text-body text-text" data-testid="what-changed">
          <span className="text-muted">{VERDICT.whatChanged}</span>
          {view.edit.summary}
        </p>
      ) : null}
      <DiffViewer patch={view.diff} collapsedByDefault={mobile} onExpand={() => track('demo.evidence_open', { which: 'diff' })} hunkCounts={hunkCounts} activeHunk={activeHunk} onHunk={onHunk} />
    </Panel>
  )
}

export function DriftPanel({ v, onRebless, reblessed }: { v: Verdict; onRebless: (p: PairVerdict) => Promise<unknown>; reblessed: Set<string> }) {
  const driftPairs = v.pairs.filter((p) => p.drift.length > 0)
  if (!driftPairs.length) return null
  // One card per fact; the pairs it explains are listed under it with their Re-bless.
  const byFact = new Map<string, { check: PairVerdict['drift'][number]; pairs: PairVerdict[] }>()
  for (const p of driftPairs)
    for (const d of p.drift) {
      const e = byFact.get(d.fact_id) ?? { check: d, pairs: [] }
      e.pairs.push(p)
      byFact.set(d.fact_id, e)
    }
  return (
    <Panel id="drift-title" title={VERDICT.drift} aside={<p className="text-meta text-muted">{VERDICT.driftSource}</p>}>
      {[...byFact.values()].map(({ check, pairs }) => (
        <div key={check.fact_id} className="flex flex-col gap-3">
          <DriftCard check={check} onSourceClick={() => track('demo.evidence_open', { which: 'drift' })} />
          {check.changed && check.status === 'checked' ? (
            <>
              <p className="text-meta text-muted">
                {VERDICT.reblessIntro} {undoHint.rebless}
              </p>
              <ul className="flex flex-col border border-border">
                {pairs.map((p) => (
                  <li key={pairKey(p)} className="flex flex-col gap-3 border-t border-border px-4 py-3 first:border-t-0 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-body text-text">{p.task_label || p.task_input}</p>
                      <p className="text-meta text-muted">{p.reason}</p>
                    </div>
                    <ActionButton
                      kind="rebless"
                      size="sm"
                      align="end"
                      subject={p.task_label || p.task_input}
                      onConfirm={() => onRebless(p)}
                      done={reblessed.has(pairKey(p))}
                      doneLabel={VERDICT.reblessed}
                      onClick={() => track('demo.action_click', { which: 'rebless' })}
                      hideHint
                    />
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ))}
    </Panel>
  )
}
