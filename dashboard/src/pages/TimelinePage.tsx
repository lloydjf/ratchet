import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/select'
import { useMeta } from '../api/hooks'
import { TIMELINE_KINDS, useLiveEvent, useLiveLimited, useLiveStatus } from '../api/live'
import type { Decision, Event } from '../api/types'
import { EMPTY, LIVE_LIMITED } from '../copy'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { SkeletonRows } from '../components/Skeleton'
import { TimelineItem } from '../components/TimelineItem'
import { dayHeading, dayKey } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'

const KIND_FILTERS = {
  all: { label: 'All', kinds: null },
  edits: { label: 'Edits', kinds: ['edit.detected'] },
  verdicts: { label: 'Verdicts', kinds: ['verdict.decided'] },
  blessings: { label: 'Blessings', kinds: ['trace.blessed', 'pair.reblessed'] },
  actions: { label: 'Actions', kinds: ['verdict.acted', 'verdict.rolled_back'] },
} as const
type KindFilter = keyof typeof KIND_FILTERS

function skillsOf(e: Event): string[] {
  const p = e.payload
  if (Array.isArray(p.affected_skills)) return p.affected_skills as string[]
  if (Array.isArray(p.skills_invoked)) return p.skills_invoked as string[]
  if (typeof p.skill === 'string') return [p.skill]
  return []
}

export default function TimelinePage() {
  useDocumentTitle(['Timeline'])
  const [params, setParams] = useSearchParams()
  const kind = (params.get('kind') as KindFilter) in KIND_FILTERS ? (params.get('kind') as KindFilter) : 'all'
  const skill = params.get('skill') ?? ''
  const decision = (params.get('decision') ?? '') as Decision | ''
  const [live, setLive] = useState(true)
  const [liveItems, setLiveItems] = useState<Event[]>([])
  const status = useLiveStatus()
  const limited = useLiveLimited()
  const { data: meta } = useMeta()

  // Own key (not ['events']) so live events prepend instead of refetching the list.
  const q = useQuery({ queryKey: ['timeline'], queryFn: () => api().listEvents({ limit: 200 }), staleTime: 60_000 })

  useLiveEvent((e) => {
    if (!TIMELINE_KINDS.has(e.kind)) return
    setLiveItems((xs) => (xs.some((x) => x.id === e.id) ? xs : [e, ...xs]))
  }, live)

  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params)
    if (v) next.set(k, v)
    else next.delete(k)
    setParams(next, { replace: true })
  }

  const all = useMemo(() => {
    const base = (q.data?.items ?? []).filter((e) => TIMELINE_KINDS.has(e.kind))
    const seen = new Set(base.map((e) => e.id))
    return [...liveItems.filter((e) => !seen.has(e.id)), ...base].sort((a, b) => b.id - a.id)
  }, [q.data, liveItems])

  const skills = useMemo(() => [...new Set(all.flatMap(skillsOf))].sort(), [all])

  const filtered = useMemo(() => {
    const kinds = KIND_FILTERS[kind].kinds as readonly string[] | null
    return all.filter((e) => {
      if (kinds && !kinds.includes(e.kind)) return false
      if (skill && !skillsOf(e).includes(skill)) return false
      if (decision && (e.kind !== 'verdict.decided' || e.payload.decision !== decision)) return false
      return true
    })
  }, [all, kind, skill, decision])

  const groups = useMemo(() => {
    const m = new Map<string, Event[]>()
    for (const e of filtered) {
      const k = dayKey(e.ts)
      m.set(k, [...(m.get(k) ?? []), e])
    }
    return [...m.entries()]
  }, [filtered])

  const liveLabel = !live ? 'Paused' : limited ? LIVE_LIMITED : status === 'paused' ? 'Paused while this tab is hidden' : status === 'polling' ? 'Live (polling)' : status === 'open' ? 'Live' : 'Connecting…'

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold text-text">Timeline</h1>
        <p className="text-body text-muted">Every edit, verdict, blessing and action for this agent, newest first.</p>
      </header>

      <div className="sticky top-12 z-20 -mx-4 flex flex-col gap-3 border-b border-border bg-bg px-4 py-3 md:top-0 md:-mx-8 md:px-8 lg:flex-row lg:items-center lg:justify-between">
        <div role="radiogroup" aria-label="Kind" className="inline-flex flex-wrap gap-px self-start border border-border bg-border">
          {(Object.keys(KIND_FILTERS) as KindFilter[]).map((k) => (
            <button
              key={k}
              type="button"
              role="radio"
              aria-checked={kind === k}
              onClick={() => setParam('kind', k === 'all' ? '' : k)}
              className={`min-h-8 px-3 text-body max-md:min-h-11 ${kind === k ? 'bg-surface-2 text-text' : 'bg-surface text-muted hover:text-text'}`}
            >
              {KIND_FILTERS[k].label}
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-meta text-muted">
            Skill
            <select value={skill} onChange={(e) => setParam('skill', e.target.value)} className="min-h-8 rounded-sm border border-border-strong bg-surface px-2 text-body text-text max-md:min-h-11">
              <option value="">All skills</option>
              {skills.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-meta text-muted">
            Decision
            <select value={decision} onChange={(e) => setParam('decision', e.target.value)} className="min-h-8 rounded-sm border border-border-strong bg-surface px-2 text-body text-text max-md:min-h-11">
              <option value="">Any</option>
              <option value="BLOCK">BLOCK</option>
              <option value="WARN">WARN</option>
              <option value="PASS">PASS</option>
            </select>
          </label>
          <button
            type="button"
            role="switch"
            aria-checked={live}
            onClick={() => setLive((l) => !l)}
            className={`inline-flex min-h-8 items-center gap-2 rounded-sm border border-border-strong bg-surface px-3 text-body max-md:min-h-11 ${live ? 'text-text' : 'text-muted'}`}
            data-testid="live-toggle"
          >
            <span aria-hidden="true" className={`block h-1.5 w-1.5 rounded-full ${live ? 'bg-pass' : 'border border-border-strong'}`} />
            {liveLabel}
          </button>
        </div>
      </div>

      {q.isLoading ? (
        <SkeletonRows rows={8} label="Loading timeline" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !all.length ? (
        <EmptyState title={EMPTY.timeline} action={{ label: 'Simulate a scenario', to: '/' }} />
      ) : !filtered.length ? (
        <EmptyState title={EMPTY.filtered} action={{ label: 'Clear filters', onClick: () => setParams(new URLSearchParams(), { replace: true }) }} />
      ) : (
        <div className="flex flex-col gap-6">
          {groups.map(([key, events]) => (
            <section key={key} aria-labelledby={`day-${key}`}>
              <h2 id={`day-${key}`} className="mb-2 text-body font-semibold text-text">
                {dayHeading(events[0]!.ts)}
              </h2>
              <ol className="divide-y divide-border border border-border bg-surface">
                {events.map((e) => (
                  <TimelineItem key={e.id} event={e} linkTraces={meta ? !meta.public_mode : false} thumbs />
                ))}
              </ol>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
