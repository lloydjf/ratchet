import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { isApiError } from '../api/client'
import { qk, useBlessRun, useMeta, useTrace, useUnbless } from '../api/hooks'
import { api } from '../api/select'
import { apiErrorCopy, BADGE, CANCEL, errorCopy, successToast, TRACE } from '../copy'
import { ActionButton } from '../components/ActionButton'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { StepList } from '../components/StepList'
import { useToast } from '../components/Toast'
import { PageSkeleton } from '../layout/AppShell'
import { dateTime } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'

export default function TracePage() {
  const { runId = '' } = useParams()
  const { data: meta } = useMeta()
  const q = useTrace(runId, meta != null && !meta.public_mode)
  const qc = useQueryClient()
  const toast = useToast()
  const unbless = useUnbless(runId)
  const bless = useBlessRun()
  const [draft, setDraft] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const t = q.data
  useDocumentTitle([TRACE.title, t?.task_label])

  if (!meta) return <PageSkeleton />
  if (meta.public_mode)
    return (
      <div className="flex flex-col gap-6">
        <h1 className="text-h1 font-semibold text-text">{TRACE.title}</h1>
        <EmptyState title={errorCopy('not_public')} action={{ label: 'Simulate a scenario', to: '/' }} />
      </div>
    )
  if (q.isLoading) return <PageSkeleton />
  if (q.isError || !t) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />

  const save = async () => {
    if (draft == null) return
    setSaving(true)
    try {
      const next = await api().putCriteria(runId, draft.split('\n').map((s) => s.trim()).filter(Boolean))
      qc.setQueryData(qk.trace(runId), next)
      setDraft(null)
      toast({ message: successToast.criteria(), tone: 'pass' })
    } catch (e) {
      toast({ message: apiErrorCopy(isApiError(e) ? e : null), tone: 'warn' })
    } finally {
      setSaving(false)
    }
  }

  const golden = t.kind === 'golden'

  return (
    <div className="flex flex-col gap-8">
      <header className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge text={TRACE.kind[t.kind] ?? TRACE.kind.live!} />
          {t.world_dependent ? <Badge text={BADGE.worldDependent} tone="drift" /> : null}
          {t.archived_reason ? <Badge text={TRACE.archived(t.archived_reason)} tone="warn" /> : null}
        </div>
        <h1 className="text-h1 font-semibold text-text">{t.task_label || TRACE.title}</h1>
        <p className="max-w-prose text-narrative text-text">{t.task_input}</p>
        <dl className="num flex flex-wrap gap-x-5 gap-y-1 text-meta">
          <div className="flex gap-1.5"><dt className="text-muted">Run</dt><dd className="font-mono text-text">{t.run_id}</dd></div>
          <div className="flex gap-1.5"><dt className="text-muted">Skills</dt><dd className="font-mono text-text">{t.skills_invoked.join(', ')}</dd></div>
          <div className="flex gap-1.5"><dt className="text-muted">Agent tier</dt><dd className="text-text">{t.agent_tier}</dd></div>
          <div className="flex gap-1.5"><dt className="text-muted">Blessed</dt><dd className="text-text">{dateTime(t.blessed_at)}</dd></div>
          <div className="flex gap-1.5"><dt className="text-muted">Harness</dt><dd className="font-mono text-text">{t.env.harness_version}</dd></div>
          <div className="flex gap-1.5"><dt className="text-muted">Artifacts</dt><dd className="font-mono text-text">{t.artifacts_ref ?? 'none'}</dd></div>
        </dl>
        {golden && !t.archived_reason ? (
          <div className="flex flex-wrap gap-2" data-testid="trace-actions">
            <ActionButton
              kind="unbless"
              subject={t.task_label || t.task_input}
              errorDisplay="toast"
              onConfirm={async () => {
                await unbless.mutateAsync()
                toast({ message: successToast.unbless(), tone: 'neutral' })
              }}
            />
          </div>
        ) : t.kind === 'live' ? (
          <div className="flex flex-wrap gap-2" data-testid="trace-actions">
            <ActionButton
              kind="bless"
              subject={t.task_label || t.task_input}
              errorDisplay="toast"
              onConfirm={async () => {
                await bless.mutateAsync(runId)
                toast({ message: successToast.bless(), tone: 'pass' })
              }}
            />
          </div>
        ) : null}
      </header>

      <section aria-labelledby="criteria-title" className="card flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 id="criteria-title" className="text-h3 font-semibold text-text">Success criteria</h2>
          {draft == null && golden ? (
            <Button size="sm" onClick={() => setDraft(t.success_criteria.join('\n'))}>{TRACE.editCriteria}</Button>
          ) : null}
        </div>
        {draft == null ? (
          <ul className="flex flex-col gap-1.5 text-body text-text">
            {t.success_criteria.map((c, i) => (
              <li key={i} className="flex gap-2"><span aria-hidden="true" className="font-mono text-muted">{i + 1}.</span>{c}</li>
            ))}
          </ul>
        ) : (
          <>
            <label className="text-meta text-muted" htmlFor="criteria">{TRACE.criteriaHint}</label>
            <textarea id="criteria" value={draft} onChange={(e) => setDraft(e.target.value)} rows={5} className="rounded-sm border border-border-strong bg-bg p-3 text-body text-text" />
            <div className="flex gap-2">
              <Button variant="primary" onClick={() => void save()} disabled={saving}>{saving ? TRACE.saving : TRACE.saveCriteria}</Button>
              <Button onClick={() => setDraft(null)}>{CANCEL}</Button>
            </div>
          </>
        )}
      </section>

      {t.world_facts.length ? (
        <section aria-labelledby="facts-title" className="flex flex-col gap-3">
          <h2 id="facts-title" className="text-h3 font-semibold text-text">World facts</h2>
          <ul className="divide-y divide-border rounded-card border border-border">
            {t.world_facts.map((f) => (
              <li key={f.fact_id} className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                <span className="text-body text-text">{f.statement}</span>
                <span className="flex gap-2">
                  {f.volatile ? <Badge text="Volatile" tone="drift" /> : <Badge text="Stable" />}
                  <span className="font-mono text-mono text-muted">from {f.source_step}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section aria-labelledby="trace-steps" className="flex flex-col gap-3">
        <h2 id="trace-steps" className="text-h3 font-semibold text-text">Steps</h2>
        <div className="rounded-card border border-border">
          <StepList rows={t.steps.map((s) => ({ step: s, differs: false }))} side="golden" highlightDiffs={false} />
        </div>
        <h3 className="text-body font-semibold text-text">Final output</h3>
        <pre className="card p-3 font-mono text-mono whitespace-pre-wrap text-text">{t.final_output}</pre>
      </section>
    </div>
  )
}
