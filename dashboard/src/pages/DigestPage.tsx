import { Fragment, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useDigest, useVerdicts } from '../api/hooks'
import type { DigestItem, MetricRow, VerdictSummary } from '../api/types'
import { DIGEST, EMPTY } from '../copy'
import { EmptyState } from '../components/EmptyState'
import { DecisionMark } from '../components/Marks'
import { ErrorState } from '../components/ErrorState'
import { PageSkeleton } from '../layout/AppShell'
import { shortDay } from '../lib/format'
import { isApiError } from '../api/client'
import { useDocumentTitle } from '../lib/useDocumentTitle'

/** Link the first mention of each skill in the prose to the verdict it refers to. */
function linkify(text: string, items: DigestItem[]): ReactNode[] {
  const targets = items.filter((i) => i.verdict_id)
  const used = new Set<string>()
  const out: ReactNode[] = []
  let rest = text
  let key = 0
  for (;;) {
    let best: { idx: number; item: DigestItem } | null = null
    for (const it of targets) {
      if (used.has(it.skill)) continue
      const idx = rest.indexOf(it.skill)
      if (idx >= 0 && (!best || idx < best.idx)) best = { idx, item: it }
    }
    if (!best) break
    out.push(<Fragment key={key++}>{rest.slice(0, best.idx)}</Fragment>)
    out.push(
      <Link key={key++} to={`/verdicts/${encodeURIComponent(best.item.verdict_id!)}`} className="link font-mono text-[0.94em]">
        {best.item.skill}
      </Link>,
    )
    used.add(best.item.skill)
    rest = rest.slice(best.idx + best.item.skill.length)
  }
  out.push(<Fragment key={key}>{rest}</Fragment>)
  return out
}

function List({ title, items, tone }: { title: string; items: DigestItem[]; tone: 'pass' | 'block' }) {
  return (
    <section className="card p-5" aria-labelledby={`dg-${tone}`}>
      <h2 id={`dg-${tone}`} className="heading-sm">{title}</h2>
      {items.length ? (
        <ul className="mt-3 flex flex-col gap-3">
          {items.map((i) => (
            <li key={i.edit_id} className="flex gap-3">
              <DecisionMark decision={tone === 'pass' ? 'PASS' : 'BLOCK'} className={`mt-0.5 ${tone === 'pass' ? 'text-pass-ink' : 'text-block-ink'}`} />
              <div className="min-w-0">
                {i.verdict_id ? (
                  <Link to={`/verdicts/${encodeURIComponent(i.verdict_id)}`} className="link font-mono text-meta font-medium">{i.skill}</Link>
                ) : (
                  <span className="font-mono text-meta font-medium text-text">{i.skill}</span>
                )}
                <p className="text-meta text-muted">{i.summary}</p>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-meta text-muted">None this week.</p>
      )}
    </section>
  )
}

/** Product-metrics table (metric, value), as `ratchet metrics` prints it. */
function Metrics({ rows }: { rows: MetricRow[] }) {
  return (
    <section className="flex flex-col gap-3" aria-labelledby="dg-metrics" data-testid="digest-metrics">
      <h2 id="dg-metrics" className="text-h3 font-semibold text-text">{DIGEST.metricsTitle}</h2>
      <div className="overflow-x-auto border border-border bg-surface">
        <table className="w-full border-collapse text-left">
          <thead className="text-meta text-muted">
            <tr>
              <th scope="col" className="px-4 py-2.5 font-medium">{DIGEST.metric}</th>
              <th scope="col" className="px-4 py-2.5 text-right font-medium">{DIGEST.value}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.metric} className="border-t border-border">
                <th scope="row" className="px-4 py-2.5 text-left text-body font-normal text-text">{r.metric}</th>
                <td className="num px-4 py-2.5 text-right font-mono text-meta text-text">{r.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function normalize(xs: (string | DigestItem)[], verdicts: VerdictSummary[] | undefined): DigestItem[] {
  return xs.map((x) => {
    if (typeof x !== 'string') return x
    const v = verdicts?.find((s) => s.verdict_id === x || s.edit_id === x)
    return { edit_id: v?.edit_id ?? x, verdict_id: v?.verdict_id ?? x, skill: v?.affected_skills.join(', ') ?? x, summary: v?.headline ?? '' }
  })
}

export default function DigestPage() {
  useDocumentTitle(['Weekly digest'])
  const q = useDigest()
  const verdicts = useVerdicts()
  if (q.isLoading) return <PageSkeleton />
  if (q.isError && isApiError(q.error) && q.error.code === 'not_found') return <EmptyState title={EMPTY.digest} action={{ label: EMPTY.digestAction, to: '/timeline' }} />
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  const d = q.data
  const merged = normalize(d.merged, verdicts.data?.items)
  const blocked = normalize(d.blocked, verdicts.data?.items)
  const items = [...merged, ...blocked]
  return (
    <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
      <article className="flex flex-col gap-4 lg:col-span-2">
        <header className="flex flex-col gap-1">
          <h1 className="text-h1 font-semibold text-text">What your agent learned this week</h1>
          <p className="num text-body text-muted">
            Week of {shortDay(d.period_start)} – {shortDay(d.period_end)}
          </p>
        </header>
        {d.narrative.split(/\n{2,}/).map((para, i) => (
          <p key={i} className="max-w-prose text-narrative text-text">
            {linkify(para, items)}
          </p>
        ))}
        <p className="text-meta text-muted">Written by Nemotron Ultra from this week’s edits and verdicts.</p>
        {d.metrics?.length ? <Metrics rows={d.metrics} /> : null}
      </article>
      <aside className="flex flex-col gap-4" aria-label="This week">
        <List title="Learned this week" items={merged} tone="pass" />
        <List title="Blocked this week" items={blocked} tone="block" />
      </aside>
    </div>
  )
}
