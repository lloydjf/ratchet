import { memo } from 'react'
import { Link } from 'react-router-dom'
import { BadgeCheck, BookOpen, GitCommitHorizontal, GitMerge, Gavel, RefreshCw, RotateCcw, CircleDot, PauseCircle, Repeat2, type LucideIcon } from 'lucide-react'
import type { Decision, Event, TierMap } from '../api/types'
import { ACTED_COPY, CONTROL } from '../copy'
import { sumTiers, time, truncate, usd } from '../lib/format'
import { DecisionChip } from './DecisionChip'

export interface TimelineView {
  icon: LucideIcon
  title: string
  summary: string
  href: string | null
  usd: number | null
  /** Tiers that incurred `usd`, e.g. "nano, ultra". */
  tiers?: string
  decision: Decision | null
  kindLabel: string
}

const str = (v: unknown, fb = '') => (typeof v === 'string' ? v : fb)
const skillsOf = (p: Record<string, unknown>) => (Array.isArray(p.affected_skills) ? (p.affected_skills as string[]).join(', ') : str(p.skill))

/** Map an event row to what the timeline shows for that event kind. */
export function describeEvent(e: Event, opts: { linkTraces?: boolean } = {}): TimelineView {
  const linkTraces = opts.linkTraces ?? true
  const p = e.payload
  const vid = str(p.verdict_id, e.ref)
  const vHref = `/verdicts/${encodeURIComponent(vid)}`
  switch (e.kind) {
    case 'trace.blessed':
      return { icon: BadgeCheck, kindLabel: 'Blessing', title: `Blessed: ${str(p.task_label, str(p.task, str(p.run_id, e.ref)))}`, summary: `Golden trace for ${Array.isArray(p.skills_invoked) ? (p.skills_invoked as string[]).join(', ') : 'this agent'}${p.world_dependent ? ' · world-dependent' : ''}`, href: linkTraces ? `/traces/${encodeURIComponent(str(p.run_id, e.ref))}` : null, usd: null, decision: null }
    case 'edit.detected':
      return { icon: GitCommitHorizontal, kindLabel: 'Edit', title: `Edit detected in ${skillsOf(p)}`, summary: str(p.summary, `Commit ${str(p.commit).slice(0, 7)} by ${str(p.author, 'agent')}`), href: `/edits/${encodeURIComponent(str(p.edit_id, e.ref))}`, usd: null, decision: null }
    case 'verdict.decided': {
      const decision = (str(p.decision) as Decision) || null
      return { icon: Gavel, kindLabel: 'Verdict', title: str(p.headline, `Verdict ${decision ?? ''} on ${skillsOf(p)}`), summary: outcomeSummary(p.n_by_outcome), href: vHref, usd: sumTiers(p.usd_by_tier as TierMap<number>), tiers: tiersOf(p.usd_by_tier as TierMap<number>), decision }
    }
    case 'verdict.acted': {
      const action = str(p.action) as keyof typeof ACTED_COPY
      const icon = action === 'merged' ? GitMerge : action === 'reverted' ? RotateCcw : PauseCircle
      // `override`: Approve on a reverted verdict re-applied the edit ("approve anyway").
      const label = p.override === true ? 'Approved anyway' : (ACTED_COPY[action] ?? 'Acted')
      return { icon, kindLabel: 'Action', title: `${label}: ${skillsOf(p) || 'skill'}`, summary: [p.actor ? `By ${str(p.actor)}` : '', p.commit ? `commit ${str(p.commit).slice(0, 7)}` : action === 'held' ? 'waiting for a human' : ''].filter(Boolean).join(' · '), href: vHref, usd: null, decision: null }
    }
    case 'verdict.rolled_back': {
      // The server's toast says whether main changed ("main no longer has this edit").
      const effect = str(p.toast).replace(/^Rolled back\.\s*/, '').replace(/\.$/, '') || 'main is unchanged'
      return { icon: RotateCcw, kindLabel: 'Action', title: `Rolled back: ${skillsOf(p) || 'skill'}`, summary: `By ${str(p.actor, 'someone')} · ${effect}`, href: vHref, usd: null, decision: null }
    }
    case 'pair.reblessed': {
      const facts = Array.isArray(p.drift_fact_ids) ? (p.drift_fact_ids as unknown[]).filter((x): x is string => typeof x === 'string') : []
      const why = facts.length ? `world changed: ${facts.join(', ')}` : 'world changed'
      return { icon: RefreshCw, kindLabel: 'Blessing', title: 'Re-blessed a changed task', summary: `The replay is the new reference; the old one is archived (${why}).`, href: linkTraces ? `/traces/${encodeURIComponent(str(p.golden, e.ref.split('/')[0] ?? ''))}` : null, usd: null, decision: null }
    }
    case 'pair.control': {
      // ADR 0023 payload: {golden, replay, control, control_outcome}; no task label, so fall back to the golden id.
      const golden = str(p.golden, e.ref)
      const task = str(p.task_label, str(p.task, golden))
      const replay = str(p.replay)
      const hasVerdict = typeof p.verdict_id === 'string'
      const href = !hasVerdict ? null : golden && replay ? `${vHref}/pairs/${encodeURIComponent(golden)}/${encodeURIComponent(replay)}` : vHref
      return { icon: Repeat2, kindLabel: 'Control', title: CONTROL.timeline(str(p.control_outcome) || null, task), summary: CONTROL.timelineSummary(str(p.control_outcome) || null), href, usd: null, decision: null }
    }
    case 'digest.written':
      return { icon: BookOpen, kindLabel: 'Digest', title: `Weekly digest written`, summary: str(p.period, 'This week'), href: '/digest', usd: null, decision: null }
    default:
      return { icon: CircleDot, kindLabel: 'Event', title: e.kind, summary: truncate(JSON.stringify(p), 80), href: null, usd: null, decision: null }
  }
}

function tiersOf(m: TierMap<number> | undefined): string {
  if (!m) return ''
  return (['nano', 'super', 'ultra'] as const).filter((t) => (m[t] ?? 0) > 0).join(', ')
}

function outcomeSummary(n: unknown): string {
  if (!n || typeof n !== 'object') return ''
  const m = n as Record<string, number>
  const parts: string[] = []
  const label: Record<string, string> = { regression: 'got worse', uncertain: 'not sure', drift: 'world changed', improved: 'better', pass: 'same result', failed: 'could not replay' }
  for (const k of ['regression', 'uncertain', 'drift', 'improved', 'pass', 'failed']) if (m[k]) parts.push(`${m[k]} ${label[k]}`)
  return parts.join(' · ')
}

/** One timeline row: icon by kind, title, one-line summary, time, cost, link. */
export const TimelineItem = memo(function TimelineItem({ event, linkTraces = true }: { event: Event; linkTraces?: boolean }) {
  const v = describeEvent(event, { linkTraces })
  const Icon = v.icon
  const body = (
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-sm border border-border bg-surface-2 text-muted" aria-hidden="true">
        <Icon size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          {v.decision ? <DecisionChip decision={v.decision} size="sm" /> : null}
          <p className="min-w-0 text-body font-medium text-text">{v.title}</p>
        </div>
        {v.summary ? <p className="mt-0.5 truncate text-meta text-muted">{v.summary}</p> : null}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-0.5 text-right">
        <time dateTime={event.ts} className="num font-mono text-mono text-muted">
          {time(event.ts)}
        </time>
        {v.usd != null && v.usd > 0 ? <span className="num font-mono text-mono text-muted">{usd(v.usd)} ({v.tiers || 'judges'})</span> : null}
        <span className="sr-only">{v.kindLabel}</span>
      </div>
    </div>
  )
  return (
    <li data-kind={event.kind} data-testid="timeline-item">
      {v.href ? (
        <Link to={v.href} className="block rounded-sm px-3 py-3 hover:bg-surface">
          {body}
        </Link>
      ) : (
        <div className="px-3 py-3">{body}</div>
      )}
    </li>
  )
})
