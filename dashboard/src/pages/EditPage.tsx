import { Link, useParams } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { useEdit, useMeta, useVerdict } from '../api/hooks'
import { DecisionChip } from '../components/DecisionChip'
import { DiffViewer } from '../components/DiffViewer'
import { ErrorState } from '../components/ErrorState'
import { PageSkeleton } from '../layout/AppShell'
import { dateTime, shortSha } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'

export default function EditPage() {
  const { id = '' } = useParams()
  const q = useEdit(id)
  const { data: meta } = useMeta()
  const verdict = useVerdict(q.data?.verdict_id ?? undefined)
  const e = q.data?.edit
  useDocumentTitle(['Edit', e?.affected_skills.join(', ')])
  if (q.isLoading) return <PageSkeleton />
  if (q.isError || !q.data || !e) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  const v = verdict.data?.verdict
  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <p className="eyebrow">Skill edit</p>
        <h1 className="text-h1 font-semibold text-text">
          Edit to <span className="font-mono">{e.affected_skills.join(', ')}</span>
        </h1>
        {e.summary ? <p className="max-w-prose text-narrative text-text">{e.summary}</p> : null}
        <dl className="num flex flex-wrap gap-x-5 gap-y-1 text-meta">
          {[
            ['Commit', <span className="font-mono">{shortSha(e.commit)}</span>],
            ['Parent on main', <span className="font-mono">{shortSha(e.parent)}</span>],
            ['Author', e.author === 'seeded' ? 'seeded scenario' : e.author],
            ['Detected', dateTime(e.detected_at)],
            ['Files', e.files.length],
          ].map(([k, val]) => (
            <div key={String(k)} className="flex gap-1.5">
              <dt className="text-muted">{k}</dt>
              <dd className="text-text">{val}</dd>
            </div>
          ))}
        </dl>
      </header>

      {q.data.verdict_id ? (
        <section className="card flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between" aria-label="Verdict for this edit">
          <div className="flex items-center gap-3">
            {v ? <DecisionChip decision={v.decision} /> : <span className="text-meta text-muted">Running…</span>}
            <p className="text-body text-text">{v?.headline ?? 'Ratchet is still checking this edit.'}</p>
          </div>
          <Link to={`/verdicts/${encodeURIComponent(q.data.verdict_id)}`} className="link inline-flex min-h-11 shrink-0 items-center gap-1 text-meta font-medium">
            Open verdict <ArrowRight size={14} aria-hidden="true" />
          </Link>
        </section>
      ) : null}

      {v?.action === 'reverted' && v.rolled_back_at ? (
        <p className="rounded-card border border-border bg-surface p-4 text-body text-text">
          This edit was rolled back. To re-apply it, run <code className="rounded-sm bg-surface-2 px-1 font-mono text-mono">ratchet approve {v.verdict_id}</code>
          {meta?.public_mode ? ' on a local Ratchet; the demo resets itself after 10 minutes.' : '.'}
        </p>
      ) : null}

      <section aria-labelledby="files-title" className="flex flex-col gap-3">
        <h2 id="files-title" className="text-h3 font-semibold text-text">
          Changed files
        </h2>
        <DiffViewer patch={q.data.diff} />
      </section>
    </div>
  )
}
