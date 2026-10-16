import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { useEditLogs, useMeta } from '../../api/hooks'
import type { Progress, Verdict, VerdictView } from '../../api/types'
import { CONTROL, DEBUG, HIDE_LOGS, SHOW_LOGS, stageLabel, VERDICT } from '../../copy'
import { CostPanel } from '../../components/CostPanel'
import { UsdTiers } from '../../components/UsdTiers'
import { duration, plural } from '../../lib/format'

function StageTimeline({ v }: { v: Verdict }) {
  const total = v.stages.reduce((a, s) => a + s.elapsed_ms, 0) || 1
  return (
    <section className="card p-4" aria-labelledby="stages-title">
      <h2 id="stages-title" className="eyebrow">
        Stage timeline
      </h2>
      <ol className="mt-3 flex flex-col gap-2">
        {v.stages.map((s) => {
          const skipped = s.elapsed_ms === 0
          return (
            <li key={s.stage} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1 text-meta">
              <span className={skipped ? 'text-muted' : 'text-text'}>{stageLabel(s.stage, null, null, v.runner)}</span>
              <span className="num font-mono text-mono text-muted">{skipped ? 'Not needed' : `${(s.elapsed_ms / 1000).toFixed(1)} s`}</span>
              <span className="col-span-2 block h-1 overflow-hidden rounded-full bg-surface-2" aria-hidden="true">
                {!skipped ? <span className="block h-full rounded-full bg-border-strong" style={{ width: `${Math.max(2, (s.elapsed_ms / total) * 100)}%` }} /> : null}
              </span>
            </li>
          )
        })}
      </ol>
      <p className="num mt-3 border-t border-border pt-3 text-meta text-muted">
        Edit to verdict: <span className="font-mono text-text">{duration(v.duration_s)}</span>
      </p>
    </section>
  )
}

function EvidenceQuality({ v }: { v: Verdict }) {
  const truncated = v.pairs.flatMap((p) => p.opinions).filter((o) => o.truncated).length
  const uncertain = v.pairs.filter((p) => p.outcome === 'uncertain').length
  const unavailable = v.pairs.flatMap((p) => p.drift).filter((d) => d.status === 'unavailable').length
  const controls = v.pairs.filter((p) => p.control_run_id).length
  const diverged = v.pairs.filter((p) => p.control_outcome === 'diverges').length
  const rows: [string, number, string][] = [
    ['Truncated opinions', truncated, 'Some evidence was shortened to fit a judge’s context.'],
    ['Uncertain pairs', uncertain, 'Held for a human.'],
    ['Drift checks unavailable', unavailable, 'Treated as possible regressions.'],
  ]
  return (
    <section className="card p-4" aria-labelledby="evidence-title">
      <h2 id="evidence-title" className="eyebrow">
        Evidence quality
      </h2>
      <dl className="mt-3 flex flex-col gap-2">
        {rows.map(([label, n, hint]) => (
          <div key={label} className="flex items-baseline justify-between gap-3 text-meta" title={hint}>
            <dt className={n ? 'text-text' : 'text-muted'}>{label}</dt>
            <dd className={`num font-mono font-semibold ${n ? 'text-warn-ink' : 'text-muted'}`}>{n}</dd>
          </div>
        ))}
        <div className="flex items-baseline justify-between gap-3 text-meta" title={CONTROL.evidenceHint} data-testid="evidence-controls">
          <dt className={controls ? 'text-text' : 'text-muted'}>
            {CONTROL.evidenceLabel}
            <span className="sr-only">: </span>
          </dt>
          <dd className={`num font-mono font-semibold ${diverged ? 'text-warn-ink' : controls ? 'text-text' : 'text-muted'}`}>{CONTROL.evidenceValue(controls, diverged)}</dd>
        </div>
      </dl>
    </section>
  )
}

function LogsHint({ editId }: { editId: string }) {
  return (
    <p className="text-meta text-muted" data-testid="logs-hint">
      {DEBUG.logsLabel} <code className="rounded-sm bg-surface-2 px-1 font-mono text-mono text-text">{DEBUG.logsCli(editId)}</code>
      <span className="mt-1 block">{DEBUG.logsNote}</span>
    </p>
  )
}

/** `GET /edits/{id}/logs` (operator host): the server's event and worker.log lines, verbatim. */
function WorkerLogs({ editId }: { editId: string }) {
  const q = useEditLogs(editId, true)
  if (q.isLoading) return <p className="text-meta text-muted" role="status">{DEBUG.loading}</p>
  // 403 / 404 (older backend without the route) / network: the CLI prints the same lines.
  if (q.isError || !q.data) return <LogsHint editId={editId} />
  return (
    <div className="flex flex-col gap-1">
      {q.data.truncated ? <p className="text-mono text-muted" data-testid="logs-truncated">{DEBUG.truncated}</p> : null}
      <pre className="max-h-64 overflow-auto rounded-sm bg-surface-2 p-3 font-mono text-mono whitespace-pre-wrap break-all text-text" aria-label="Worker log" data-testid="worker-log">
        {q.data.lines.length ? q.data.lines.join('\n') : DEBUG.noLines}
      </pre>
    </div>
  )
}

/**
 * Debug drawer: batch ids, runner and the worker logs. Logs come from the
 * server (operator host only); the public host gets the `ratchet logs --edit` hint instead.
 */
function DebugDrawer({ v, editId }: { v: Verdict; editId: string }) {
  const [open, setOpen] = useState(false)
  const { data: meta } = useMeta()
  const operator = meta?.public_mode === false
  return (
    <section className="card p-4" aria-labelledby="debug-title" data-testid="debug-drawer">
      <div className="flex items-center justify-between gap-3">
        <h2 id="debug-title" className="eyebrow">
          {DEBUG.title}
        </h2>
        <button type="button" aria-expanded={open} aria-controls="debug-body" onClick={() => setOpen((o) => !o)} className="inline-flex min-h-11 items-center gap-1 rounded-sm px-2 text-meta font-medium text-accent hover:bg-surface-2">
          {open ? HIDE_LOGS : SHOW_LOGS}
          <ChevronDown size={14} aria-hidden="true" className={`transition-transform duration-[160ms] ${open ? 'rotate-180' : ''}`} />
        </button>
      </div>
      {open ? (
        <div id="debug-body" className="mt-3 flex flex-col gap-3">
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-meta">
            <dt className="text-muted">{DEBUG.verdict}</dt>
            <dd className="truncate font-mono text-mono text-text">{v.verdict_id}</dd>
            <dt className="text-muted">{DEBUG.edit}</dt>
            <dd className="truncate font-mono text-mono text-text">{editId}</dd>
            <dt className="text-muted">{DEBUG.runner}</dt>
            <dd className="font-mono text-mono text-text">{v.runner || '—'}</dd>
            <dt className="text-muted">{DEBUG.batches}</dt>
            <dd className="font-mono text-mono break-all text-text" data-testid="debug-batches">{v.batch_ids.join(', ') || '—'}</dd>
          </dl>
          {operator ? <WorkerLogs editId={editId} /> : <LogsHint editId={editId} />}
        </div>
      ) : null}
    </section>
  )
}

function RunningStages({ progress }: { progress: Progress }) {
  const done = progress.stages.filter((s) => s.status === 'done').length
  return (
    <section className="card p-4" aria-labelledby="run-stages">
      <h2 id="run-stages" className="eyebrow">
        Stages
      </h2>
      <ol className="mt-3 flex flex-col gap-1.5">
        {progress.stages.map((s) => (
          <li key={s.key} className="grid grid-cols-[1rem_1fr] gap-x-1 text-meta" data-stage-status={s.status}>
            <span aria-hidden="true" className={`font-mono ${s.status === 'done' ? 'text-pass-ink' : s.status === 'active' ? 'text-accent' : 'text-muted'}`}>
              {s.status === 'done' ? '✓' : s.status === 'active' ? '›' : s.status === 'skipped' ? '–' : '·'}
            </span>
            <span className={s.status === 'active' ? 'font-semibold text-text' : s.status === 'done' ? 'text-text' : 'text-muted'}>
              {s.label}
              <span className="sr-only">: {s.status}</span>
            </span>
            {s.detail ? <span className="num col-start-2 font-mono text-mono text-muted">{s.detail}</span> : null}
          </li>
        ))}
      </ol>
      <p className="num mt-3 border-t border-border pt-3 text-meta text-muted" data-testid="running-cost">
        {VERDICT.runningCost} <UsdTiers map={progress.usd_by_tier} none="no model yet" className="text-text" /> · {VERDICT.stagesDone(plural(done, 'stage'))}
      </p>
    </section>
  )
}

/** Side column: cost panel, stage timeline, evidence quality, debug drawer. */
export function VerdictSide({ view }: { view: VerdictView }) {
  const v = view.verdict
  return (
    <aside className="flex min-w-0 flex-col gap-4" aria-label="Cost and run details">
      {v ? (
        <>
          <CostPanel usdByTier={v.cost} tokensByTier={v.tokens} counterfactualUsd={v.counterfactual_usd} replayUsdByTier={v.replay_cost} />
          <StageTimeline v={v} />
          <EvidenceQuality v={v} />
          <DebugDrawer v={v} editId={view.edit.edit_id} />
        </>
      ) : view.progress ? (
        <RunningStages progress={view.progress} />
      ) : null}
    </aside>
  )
}
