import { useMeta, useSettings } from '../api/hooks'
import { TIER_NAME, errorCopy } from '../copy'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { TierBadge } from '../components/TierBadge'
import { PageSkeleton } from '../layout/AppShell'
import { usd } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'

export default function SettingsPage() {
  useDocumentTitle(['Settings'])
  const { data: meta } = useMeta()
  const q = useSettings(meta != null && !meta.public_mode)
  if (!meta) return <PageSkeleton />
  if (meta.public_mode)
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-h1 font-semibold text-text">Settings</h1>
        <EmptyState title={errorCopy('not_public')} action={{ label: 'Simulate a scenario', to: '/' }} />
      </div>
    )
  if (q.isLoading) return <PageSkeleton />
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  const s = q.data
  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <header>
        <h1 className="text-h1 font-semibold text-text">Settings</h1>
        <p className="mt-1 text-body text-muted">Read-only. Change these in <code className="font-mono text-mono">.env</code> and restart Ratchet.</p>
      </header>
      <section className="card p-4" aria-labelledby="runtime">
        <h2 id="runtime" className="eyebrow">Runtime</h2>
        <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-y-2 text-meta">
          <dt className="text-muted">Harness</dt><dd className="font-mono text-text">{s.harness}</dd>
          <dt className="text-muted">Job runner</dt><dd className="font-mono text-text">{s.runner}</dd>
          <dt className="text-muted">Sandbox</dt><dd className="font-mono text-text">{s.sandbox}</dd>
          <dt className="text-muted">Webhook</dt><dd className="text-text">{s.webhook_configured ? 'Configured' : 'Not set'}</dd>
        </dl>
      </section>
      <section className="card p-4" aria-labelledby="models">
        <h2 id="models" className="eyebrow">Models</h2>
        <ul className="mt-3 flex flex-col gap-2">
          {(['nano', 'super', 'ultra'] as const).map((t) => (
            <li key={t} className="flex flex-wrap items-center gap-3 text-meta">
              <TierBadge tier={t} plain size="sm" />
              <span className="w-32 text-text">{TIER_NAME[t]}</span>
              <span className="font-mono text-mono text-muted">{s.models[t]}</span>
            </li>
          ))}
        </ul>
      </section>
      <section className="card p-4" aria-labelledby="caps">
        <h2 id="caps" className="eyebrow">Daily spending caps</h2>
        <dl className="mt-3 grid grid-cols-[10rem_1fr] gap-y-2 text-meta">
          {s.caps.map((c) => (
            <div key={c.purpose} className="contents">
              <dt className="text-muted">{c.purpose}</dt>
              <dd className="num font-mono text-text">{usd(c.usd_per_day)} / day</dd>
            </div>
          ))}
        </dl>
      </section>
    </div>
  )
}
