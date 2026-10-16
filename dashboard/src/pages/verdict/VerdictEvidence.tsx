import { useMemo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import type { PairVerdict, Verdict, VerdictView } from '../../api/types'
import { track } from '../../analytics'
import { firstSentence, OUTCOME_LABEL, TIER_NAME, undoHint, VERDICT } from '../../copy'
import { ActionButton } from '../../components/ActionButton'
import { DiffViewer } from '../../components/DiffViewer'
import { DriftCard } from '../../components/DriftCard'
import { PairGrid } from '../../components/PairGrid'
import { OUTCOME_ORDER, pairKey } from '../../lib/pairs'

function Section({ id, title, aside, children }: { id: string; title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id={id} className="text-h3 font-semibold text-text">
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

/** The narrative minus its opening sentence, which is already the header title. */
function useNarrativeBody(v: Verdict | null): string {
  return useMemo(() => {
    if (!v) return ''
    if (v.headline && v.narrative.startsWith(v.headline)) return v.narrative.slice(v.headline.length).trim()
    const first = firstSentence(v.narrative)
    return first === v.headline ? v.narrative.slice(first.length).trim() : v.narrative
  }, [v])
}

function DriftSection({ v, onRebless, reblessed }: { v: Verdict; onRebless: (p: PairVerdict) => Promise<unknown>; reblessed: Set<string> }) {
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
    <Section id="drift-title" title={VERDICT.drift} aside={<p className="text-meta text-muted">{VERDICT.driftSource}</p>}>
      {[...byFact.values()].map(({ check, pairs }) => (
        <div key={check.fact_id} className="flex flex-col gap-2">
          <DriftCard check={check} onSourceClick={() => track('demo.evidence_open', { which: 'drift' })} />
          {check.changed && check.status === 'checked' ? (
            <>
              <p className="text-meta text-muted">
                {VERDICT.reblessIntro} {undoHint.rebless}
              </p>
              <ul className="flex flex-col divide-y divide-border rounded-card border border-border">
                {pairs.map((p) => (
                  <li key={pairKey(p)} className="flex flex-col gap-3 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                      <p className="text-body text-text">{p.task_label || p.task_input}</p>
                      <p className="text-meta text-muted">{p.reason}</p>
                    </div>
                    <ActionButton
                      kind="rebless"
                      size="sm"
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
    </Section>
  )
}

interface VerdictEvidenceProps {
  view: VerdictView
  verdictId: string
  mobile: boolean
  onRebless: (p: PairVerdict) => Promise<unknown>
  reblessed: Set<string>
}

/** Main column: narrative, skill diff, pair grid, drift cards. */
export function VerdictEvidence({ view, verdictId, mobile, onRebless, reblessed }: VerdictEvidenceProps) {
  const v = view.verdict
  const narrativeBody = useNarrativeBody(v)
  return (
    <div className="flex min-w-0 flex-col gap-10 lg:col-span-2">
      {v ? (
        <section aria-label="Summary">
          <p className="max-w-prose text-narrative text-text" data-testid="narrative">
            {narrativeBody}
          </p>
          <p className="mt-2 text-meta text-muted">{VERDICT.writtenBy(TIER_NAME.ultra)}</p>
        </section>
      ) : null}

      <Section
        id="diff-title"
        title={VERDICT.diff}
        aside={
          <Link to={`/edits/${encodeURIComponent(view.edit.edit_id)}`} className="link text-meta">
            {VERDICT.editPage}
          </Link>
        }
      >
        {view.edit.summary ? (
          <p className="text-body text-text" data-testid="what-changed">
            <span className="text-muted">{VERDICT.whatChanged}</span>
            {view.edit.summary}
          </p>
        ) : null}
        <DiffViewer patch={view.diff} collapsedByDefault={mobile} onExpand={() => track('demo.evidence_open', { which: 'diff' })} />
      </Section>

      <Section id="pairs-title" title={v ? `${VERDICT.replayed} (${v.pairs.length})` : VERDICT.replayed} aside={v ? <OutcomeSummary pairs={v.pairs} /> : null}>
        {v ? (
          <PairGrid pairs={v.pairs} verdictId={v.verdict_id} onOpen={() => track('demo.evidence_open', { which: 'pair' })} />
        ) : (
          <PairGrid pairs={[]} verdictId={verdictId} loading />
        )}
      </Section>

      {v ? <DriftSection v={v} onRebless={onRebless} reblessed={reblessed} /> : null}
    </div>
  )
}
