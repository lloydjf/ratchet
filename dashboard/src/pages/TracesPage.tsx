import { useDeferredValue, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useBlessRun, useMeta, useRuns, useTraces } from '../api/hooks'
import { TIERS, type RunView, type Tier, type TraceSummary } from '../api/types'
import { BADGE, EMPTY, errorCopy, successToast, TIER_SHORT, TRACES, undoHint } from '../copy'
import { ActionButton } from '../components/ActionButton'
import { Badge } from '../components/Badge'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { SkeletonRows } from '../components/Skeleton'
import { TierBadge } from '../components/TierBadge'
import { useToast } from '../components/Toast'
import { dateTime, truncate } from '../lib/format'
import { PageSkeleton } from '../layout/AppShell'
import { useDocumentTitle } from '../lib/useDocumentTitle'

const TH = 'px-2 py-2.5 font-medium'

function Table({ minWidth, head, children }: { minWidth: string; head: ReactNode; children: ReactNode }) {
  return (
    <div className="overflow-x-auto border border-border bg-surface">
      <table className={`w-full ${minWidth} border-collapse text-left`}>
        <thead className="text-meta text-muted">
          <tr>{head}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  )
}

function TaskCell({ runId, label, preview }: { runId: string; label: string; preview: string }) {
  return (
    <td className="px-4 py-3">
      <Link to={`/traces/${encodeURIComponent(runId)}`} className="link text-body font-medium" title={preview}>
        {label || truncate(preview, 60)}
      </Link>
      <p className="text-meta text-muted">{truncate(preview, 80)}</p>
    </td>
  )
}

function GoldenTable({ items }: { items: TraceSummary[] }) {
  return (
    <Table
      minWidth="min-w-[720px]"
      head={
        <>
          <th scope="col" className="px-4 py-2.5 font-medium">Task</th>
          <th scope="col" className={TH}>Skills</th>
          <th scope="col" className={TH}>World</th>
          <th scope="col" className={`${TH} text-right`}>Facts</th>
          <th scope="col" className={TH}>Last replayed</th>
          <th scope="col" className="px-4 py-2.5 font-medium">Blessed</th>
        </>
      }
    >
      {items.map((t) => (
        <tr key={t.run_id} className="border-t border-border hover:bg-surface">
          <TaskCell runId={t.run_id} label={t.task_label} preview={t.task_input} />
          <td className="px-2 py-3 font-mono text-mono text-text">{t.skills_invoked.join(', ')}</td>
          <td className="px-2 py-3">{t.world_dependent ? <Badge text={BADGE.worldDependent} tone="drift" /> : <span className="text-meta text-muted">No</span>}</td>
          <td className="num px-2 py-3 text-right font-mono text-meta text-text">{t.n_facts}</td>
          <td className="num px-2 py-3 text-meta text-muted">{dateTime(t.last_replayed_at)}</td>
          <td className="num px-4 py-3 text-meta text-muted">{dateTime(t.blessed_at)}</td>
        </tr>
      ))}
    </Table>
  )
}

const isTier = (t: string): t is Tier => (TIERS as readonly string[]).includes(t)

/** `GET /runs?unblessed=1`: captured and live runs, each with a confirmed Bless. */
function RecentRuns({ items }: { items: RunView[] }) {
  const bless = useBlessRun()
  const toast = useToast()
  return (
    <div className="flex flex-col gap-2">
      <Table
        minWidth="min-w-[760px]"
        head={
          <>
            <th scope="col" className="px-4 py-2.5 font-medium">Task</th>
            <th scope="col" className={TH}>Source</th>
            <th scope="col" className={TH}>Tier</th>
            <th scope="col" className={`${TH} text-right`}>Steps</th>
            <th scope="col" className={TH}>Last activity</th>
            <th scope="col" className="px-4 py-2.5 font-medium"><span className="sr-only">Action</span></th>
          </>
        }
      >
        {items.map((r) => (
          <tr key={r.run_id} className="border-t border-border hover:bg-surface" data-testid="run-row" data-run-id={r.run_id}>
            <TaskCell runId={r.run_id} label={r.task_label} preview={r.task_preview} />
            <td className="px-2 py-3"><Badge text={TRACES.source[r.source] ?? r.source} /></td>
            <td className="px-2 py-3">
              {isTier(r.agent_tier) ? (
                <span className="inline-flex items-center gap-2 text-meta text-text"><TierBadge tier={r.agent_tier} plain size="sm" />{TIER_SHORT[r.agent_tier]}</span>
              ) : (
                <span className="font-mono text-mono text-text">{r.agent_tier}</span>
              )}
            </td>
            <td className="num px-2 py-3 text-right font-mono text-meta text-text" aria-label={TRACES.steps(r.n_steps)}>{r.n_steps}</td>
            <td className="num px-2 py-3 text-meta text-muted">{dateTime(r.last_activity_at)}</td>
            <td className="px-4 py-3 text-right">
              {r.blessed ? (
                <Badge text={TRACES.blessed} tone="pass" />
              ) : (
                <ActionButton
                  kind="bless"
                  size="sm"
                  hideHint
                  errorDisplay="toast"
                  subject={r.task_label || truncate(r.task_preview, 60)}
                  onConfirm={async () => {
                    await bless.mutateAsync(r.run_id)
                    toast({ message: successToast.bless(), tone: 'pass', link: { label: TRACES.openTrace, to: `/traces/${encodeURIComponent(r.run_id)}` } })
                  }}
                />
              )}
            </td>
          </tr>
        ))}
      </Table>
      <p className="text-mono text-muted">{undoHint.bless}</p>
    </div>
  )
}

export default function TracesPage() {
  const { data: meta } = useMeta()
  const [params, setParams] = useSearchParams()
  const search = params.get('q') ?? ''
  const live = params.get('kind') === 'live'
  useDocumentTitle([live ? TRACES.liveTitle : TRACES.title])
  const deferred = useDeferredValue(search)
  const operator = meta != null && !meta.public_mode
  const goldens = useTraces({ kind: 'golden', search: deferred || undefined }, operator && !live)
  const runs = useRuns({ unblessed: true, limit: 50 }, operator && live)
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: key === 'q' })
  }
  // An empty state is one sentence and one action.
  const empty = search
    ? { title: TRACES.noMatch, action: { label: TRACES.clearSearch, onClick: () => setParam('q', null) } }
    : live
      ? { title: TRACES.noRuns, action: { label: TRACES.showBlessed, onClick: () => setParam('kind', null) } }
      : { title: EMPTY.traces, action: { label: TRACES.showRuns, onClick: () => setParam('kind', 'live') } }

  if (!meta) return <PageSkeleton />
  if (meta.public_mode)
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-h1 font-semibold text-text">{TRACES.title}</h1>
        <EmptyState title={errorCopy('not_public')} action={{ label: 'Simulate a scenario', to: '/' }} />
      </div>
    )

  const q = live ? runs : goldens
  // /runs has no search parameter; filter the (at most 50) rows here.
  const needle = deferred.trim().toLowerCase()
  const runItems = (runs.data?.items ?? []).filter((r) => !needle || r.task_label.toLowerCase().includes(needle) || r.task_preview.toLowerCase().includes(needle))
  const goldenItems = goldens.data?.items ?? []
  const count = live ? runItems.length : goldenItems.length

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-h1 font-semibold text-text">{live ? TRACES.liveTitle : TRACES.title}</h1>
          <p className="mt-1 text-body text-muted">{live ? TRACES.liveIntro : TRACES.intro}</p>
          <button type="button" onClick={() => setParam('kind', live ? null : 'live')} className="link mt-1 inline-flex min-h-11 items-center text-meta font-medium" data-testid="traces-kind-toggle">
            {live ? TRACES.showBlessed : TRACES.showRuns}
          </button>
        </div>
        <label className="relative block w-full sm:w-72">
          <span className="sr-only">{TRACES.search}</span>
          <Search size={16} aria-hidden="true" className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-muted" />
          <input
            type="search"
            value={search}
            onChange={(e) => setParam('q', e.target.value)}
            placeholder={TRACES.search}
            className="min-h-11 w-full rounded-sm border border-border-strong bg-surface pr-3 pl-9 text-body text-text placeholder:text-muted"
          />
        </label>
      </header>
      {q.isLoading ? (
        <SkeletonRows rows={8} label={live ? 'Loading recent runs' : 'Loading golden traces'} />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !count ? (
        <EmptyState title={empty.title} action={empty.action} />
      ) : live ? (
        <RecentRuns items={runItems} />
      ) : (
        <GoldenTable items={goldenItems} />
      )}
    </div>
  )
}
