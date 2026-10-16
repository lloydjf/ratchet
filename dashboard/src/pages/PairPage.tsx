import { useCallback, useMemo, useRef, useState, type MouseEvent } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowDownToLine, ArrowLeft, ArrowRight, ChevronDown } from 'lucide-react'
import { responseToast, useMeta, usePair, useRebless } from '../api/hooks'
import type { PairVerdict, Trace } from '../api/types'
import { track } from '../analytics'
import { BADGE, CONTROL, DIFFERENCES_ONLY, JUMP_TO_DIFF, OUTCOME_LABEL, successToast } from '../copy'
import { Badge } from '../components/Badge'
import { Button } from '../components/Button'
import { DecisionChip } from '../components/DecisionChip'
import { DriftCard } from '../components/DriftCard'
import { ErrorState } from '../components/ErrorState'
import { JudgeTrail, cascadeOrder } from '../components/JudgeTrail'
import { Level0Card } from '../components/Level0Card'
import { OpinionCard } from '../components/OpinionCard'
import { ControlMarker, PairOutcome } from '../components/PairGrid'
import { StepList, type StepRow } from '../components/StepList'
import { useToast } from '../components/Toast'
import { alignSteps } from '../lib/align'
import { dateTime, shortSha, usd } from '../lib/format'
import { changedArgsCopy, level0Copy, pairCostTiers } from '../lib/pairs'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useIsMobile } from '../lib/useMediaQuery'

function lineDiff(a: string, b: string) {
  const al = a.split('\n')
  const bl = b.split('\n')
  const bs = new Set(bl)
  const as = new Set(al)
  return { golden: al.map((t) => ({ t, gone: !bs.has(t) })), replay: bl.map((t) => ({ t, added: !as.has(t) })) }
}

function FinalOutputs({ golden, replay }: { golden: Trace; replay: Trace }) {
  const d = lineDiff(golden.final_output, replay.final_output)
  const same = golden.final_output === replay.final_output
  return (
    <section aria-labelledby="final-title" className="flex flex-col gap-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="final-title" className="text-h3 font-semibold text-text">
          Final output
        </h2>
        <p className="text-meta text-muted">{same ? 'Identical' : 'Lines that differ are marked'}</p>
      </div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {(['golden', 'replay'] as const).map((side) => (
          <div key={side} className="card overflow-hidden">
            <p className="border-b border-border px-3 py-2 text-mono text-muted">{side === 'golden' ? 'Golden (original)' : 'Replay (new skill)'}</p>
            <pre className="p-3 font-mono text-mono whitespace-pre-wrap break-words text-text">
              {side === 'golden'
                ? d.golden.map((l, i) => (
                    <span key={i} className={`block ${l.gone ? 'bg-[var(--diff-del)] text-block-ink' : ''}`}>
                      {l.gone ? <span className="sr-only">Missing in replay: </span> : null}
                      {l.t || ' '}
                    </span>
                  ))
                : d.replay.map((l, i) => (
                    <span key={i} className={`block ${l.added ? 'bg-[var(--diff-add)] text-pass-ink' : ''}`}>
                      {l.added ? <span className="sr-only">New in replay: </span> : null}
                      {l.t || ' '}
                    </span>
                  ))}
            </pre>
          </div>
        ))}
      </div>
    </section>
  )
}

/**
 * ADR 0023 control replay, collapsible. Operator host only: the trace route is hidden on
 * the public host, which shows just the marker in the header.
 */
function ControlRun({ pair }: { pair: PairVerdict }) {
  const [open, setOpen] = useState(false)
  const runId = pair.control_run_id!
  return (
    <section className="card p-4" aria-labelledby="control-title" data-testid="control-run">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="control-title" className="text-h3 font-semibold text-text">
            {CONTROL.sectionTitle}
          </h2>
          <ControlMarker p={pair} />
        </div>
        <button type="button" aria-expanded={open} aria-controls="control-body" onClick={() => setOpen((o) => !o)} className="inline-flex min-h-11 items-center gap-1 rounded-sm px-2 text-meta font-medium text-accent hover:bg-surface-2">
          {open ? 'Hide' : 'Show'}
          <ChevronDown size={14} aria-hidden="true" className={`transition-transform duration-[160ms] ${open ? 'rotate-180' : ''}`} />
        </button>
      </div>
      {open ? (
        <div id="control-body" className="mt-3 flex flex-col gap-2">
          <p className="max-w-prose text-body text-text">{CONTROL.sectionIntro}</p>
          <p className="max-w-prose text-meta text-muted">{pair.control_outcome === 'diverges' ? CONTROL.divergesHint : pair.control_outcome === 'agrees' ? CONTROL.agreesHint : null}</p>
          <dl className="num flex flex-wrap gap-x-5 gap-y-1 text-meta">
            <div className="flex gap-1.5">
              <dt className="text-muted">{CONTROL.run}</dt>
              <dd className="font-mono text-text">{runId}</dd>
            </div>
          </dl>
          <Link to={`/traces/${encodeURIComponent(runId)}`} className="link inline-flex min-h-11 items-center gap-1 self-start text-meta font-medium" data-testid="control-trace-link">
            {CONTROL.openTrace} <ArrowRight size={14} aria-hidden="true" />
          </Link>
        </div>
      ) : null}
    </section>
  )
}

export default function PairPage() {
  const { id = '', golden = '', replay = '' } = useParams()
  const q = usePair(id, golden, replay)
  const navigate = useNavigate()
  const mobile = useIsMobile()
  const toast = useToast()
  const rebless = useRebless()
  const { data: meta } = useMeta()
  const operator = meta?.public_mode === false
  const [reblessed, setReblessed] = useState(false)
  const [tab, setTab] = useState<'golden' | 'replay'>('replay')
  const [diffOnly, setDiffOnly] = useState(false)
  const [focusStep, setFocusStep] = useState<string | null>(null)
  const gRef = useRef<HTMLDivElement>(null)
  const rRef = useRef<HTMLDivElement>(null)
  const colsRef = useRef<HTMLDivElement>(null)
  const syncing = useRef<HTMLElement | null>(null)

  const data = q.data
  const rows = useMemo(() => (data ? alignSteps(data.golden.steps, data.replay.steps) : []), [data])
  const gRows: StepRow[] = useMemo(() => rows.map((r) => ({ step: r.golden, differs: r.differs })), [rows])
  const rRows: StepRow[] = useMemo(() => rows.map((r) => ({ step: r.replay, differs: r.differs })), [rows])

  useDocumentTitle(data ? [OUTCOME_LABEL[data.pair.outcome], data.pair.task_label || data.golden.task_label, data.decision] : ['Pair'])

  const sync = useCallback((src: HTMLDivElement | null, dst: HTMLDivElement | null) => {
    if (!src || !dst || syncing.current === src) return
    const srcRows = src.querySelectorAll<HTMLElement>('[data-row]')
    let idx = 0
    let frac = 0
    for (const el of srcRows) {
      if (el.offsetTop + el.offsetHeight > src.scrollTop) {
        idx = Number(el.dataset.row)
        frac = Math.max(0, (src.scrollTop - el.offsetTop) / Math.max(1, el.offsetHeight))
        break
      }
    }
    const target = dst.querySelector<HTMLElement>(`[data-row="${idx}"]`)
    if (!target) return
    syncing.current = dst
    dst.scrollTop = target.offsetTop + frac * target.offsetHeight
    requestAnimationFrame(() => {
      syncing.current = null
    })
  }, [])

  const scrollToRow = useCallback((idx: number) => {
    for (const el of [gRef.current, rRef.current]) {
      const row = el?.querySelector<HTMLElement>(`[data-row="${idx}"]`)
      if (el && row) {
        syncing.current = el
        el.scrollTop = Math.max(0, row.offsetTop - 8)
      }
    }
    requestAnimationFrame(() => {
      syncing.current = null
    })
    colsRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [])

  const jumpToDiff = () => {
    const idx = rows.findIndex((r) => r.differs)
    if (idx < 0) return
    const r = rows[idx]!
    setFocusStep((r.replay ?? r.golden)?.step_id ?? null)
    if (mobile) setTab(r.replay ? 'replay' : 'golden')
    requestAnimationFrame(() => scrollToRow(idx))
  }

  const showStep = (stepId: string) => {
    track('demo.evidence_open', { which: 'trail' })
    const idx = rows.findIndex((r) => r.replay?.step_id === stepId || r.golden?.step_id === stepId)
    if (idx < 0) return
    setFocusStep(stepId)
    if (mobile) setTab(rows[idx]!.replay?.step_id === stepId ? 'replay' : 'golden')
    requestAnimationFrame(() => scrollToRow(idx))
  }

  const back = (e: MouseEvent) => {
    const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0
    if (idx > 0) {
      e.preventDefault()
      navigate(-1)
    }
  }

  if (q.isLoading)
    return (
      <div className="flex flex-col gap-4" aria-busy="true" role="status" aria-label="Loading pair">
        <span className="skeleton block h-5 w-40" />
        <span className="skeleton block h-9 w-3/4" />
        <div className="grid gap-3 md:grid-cols-2">
          <span className="skeleton block h-96" />
          <span className="skeleton block h-96" />
        </div>
      </div>
    )
  if (q.isError || !data) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />

  const { pair, golden: g, replay: r } = data
  const l0 = level0Copy(pair)
  const changed = changedArgsCopy(pair)
  const nDiff = rows.filter((x) => x.differs).length

  return (
    <div className="flex flex-col gap-8">
      <nav aria-label="Breadcrumb">
        <Link to={`/verdicts/${encodeURIComponent(id)}`} onClick={back} className="link inline-flex min-h-11 items-center gap-1.5 text-meta font-medium">
          <ArrowLeft size={14} aria-hidden="true" />
          Back to the verdict
          {data.decision ? <DecisionChip decision={data.decision} size="sm" className="ml-1" /> : null}
        </Link>
      </nav>

      <header className="flex flex-col gap-3" data-testid="pair-header">
        <div className="flex flex-wrap items-center gap-3">
          <PairOutcome p={pair} />
          <JudgeTrail opinions={pair.opinions} decidedBy={pair.decided_by} drift={pair.drift} />
          <span className="num font-mono text-mono text-muted">
            {usd(pair.usd)} ({pairCostTiers(pair)})
          </span>
          <ControlMarker p={pair} />
          {data.recorded ? <Badge text={BADGE.recorded} tone="neutral" hint={BADGE.recordedHint} /> : null}
        </div>
        <h1 className="text-h1 font-semibold text-text">{pair.task_label || g.task_label || 'Replayed task'}</h1>
        <p className="max-w-prose text-body text-muted">
          <span className="text-text">Task: </span>
          {pair.task_input || g.task_input}
        </p>
        {l0 ? <p className="text-body font-medium text-block-ink">{l0}</p> : changed ? <p className="text-body text-text">{changed}</p> : null}
        <p className="text-body text-text">{pair.reason}</p>
        <dl className="num flex flex-wrap gap-x-5 gap-y-1 text-meta">
          <div className="flex gap-1.5">
            <dt className="text-muted">Golden</dt>
            <dd className="font-mono text-text">{g.run_id}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted">Replay</dt>
            <dd className="font-mono text-text">{r.run_id}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted">Blessed</dt>
            <dd className="text-text">{dateTime(g.blessed_at ?? g.created_at)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted">Candidate</dt>
            <dd className="font-mono text-text">{shortSha(r.candidate_commit)}</dd>
          </div>
          <div className="flex gap-1.5">
            <dt className="text-muted">Agent tier</dt>
            <dd className="text-text">{g.agent_tier} (same as the original)</dd>
          </div>
        </dl>
      </header>

      <Level0Card finding={pair.level0} decidedBy={pair.decided_by} outcome={pair.outcome} onStep={showStep} />

      <section aria-labelledby="steps-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="steps-title" className="text-h3 font-semibold text-text">
              Steps
            </h2>
            <p className="num text-meta text-muted">
              {nDiff ? `${nDiff} of ${rows.length} steps differ` : 'No steps differ'}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {mobile ? (
              <label className="inline-flex min-h-11 cursor-pointer items-center gap-2 rounded-sm border border-border-strong px-3 text-meta text-text">
                <input type="checkbox" checked={diffOnly} onChange={(e) => setDiffOnly(e.target.checked)} className="h-4 w-4 accent-[var(--accent)]" />
                {DIFFERENCES_ONLY}
              </label>
            ) : null}
            <Button variant="secondary" size="sm" onClick={jumpToDiff} disabled={!nDiff} data-testid="jump-to-diff">
              <ArrowDownToLine size={16} aria-hidden="true" />
              {JUMP_TO_DIFF}
            </Button>
          </div>
        </div>

        <div ref={colsRef} className="scroll-mt-28">
          {mobile ? (
            <div>
              <div role="tablist" aria-label="Run" className="grid grid-cols-2 rounded-sm border border-border bg-surface p-1">
                {(['golden', 'replay'] as const).map((t) => (
                  <button
                    key={t}
                    role="tab"
                    type="button"
                    id={`tab-${t}`}
                    aria-selected={tab === t}
                    aria-controls={`panel-${t}`}
                    onClick={() => setTab(t)}
                    className={`min-h-11 rounded-[6px] text-meta font-semibold ${tab === t ? 'bg-surface-2 text-text' : 'text-muted'}`}
                  >
                    {t === 'golden' ? 'Golden' : 'Replay'}
                  </button>
                ))}
              </div>
              <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="mt-2">
                <StepList rows={tab === 'golden' ? gRows : rRows} side={tab} focusStep={focusStep} differencesOnly={diffOnly} ref={tab === 'golden' ? gRef : rRef} className="relative" />
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              {(['golden', 'replay'] as const).map((side) => (
                <div key={side} className="min-w-0 rounded-card border border-border bg-bg">
                  <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
                    <p className="text-meta font-semibold text-text">{side === 'golden' ? 'Golden (original run)' : 'Replay (with the new skill)'}</p>
                    <p className="truncate font-mono text-mono text-muted">{side === 'golden' ? g.run_id : r.run_id}</p>
                  </div>
                  <StepList
                    ref={side === 'golden' ? gRef : rRef}
                    rows={side === 'golden' ? gRows : rRows}
                    side={side}
                    focusStep={focusStep}
                    maxHeight="min(70vh, 720px)"
                    className="relative"
                    onScroll={() => (side === 'golden' ? sync(gRef.current, rRef.current) : sync(rRef.current, gRef.current))}
                  />
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <FinalOutputs golden={g} replay={r} />

      {pair.opinions.length ? (
        <section aria-labelledby="opinions-title" className="flex flex-col gap-3">
          <h2 id="opinions-title" className="text-h3 font-semibold text-text">
            What each judge said
          </h2>
          <div className="grid grid-cols-1 items-start gap-3 lg:grid-cols-2">
            {cascadeOrder(pair.opinions).map((o) => (
              <OpinionCard key={o.tier} opinion={o} onStepClick={showStep} />
            ))}
          </div>
        </section>
      ) : (
        <section className="card p-4" aria-label="Level 0">
          <p className="text-body text-text">No model was needed for this pair. Level 0 compares the actions each run took, so a new irreversible call decides it directly.</p>
        </section>
      )}

      {pair.control_run_id && operator ? <ControlRun pair={pair} /> : null}

      {pair.drift.length ? (
        <section aria-labelledby="pair-drift-title" className="flex flex-col gap-3">
          <h2 id="pair-drift-title" className="text-h3 font-semibold text-text">
            World drift
          </h2>
          {pair.drift.map((d) => (
            <DriftCard
              key={d.fact_id}
              check={d}
              task={pair.task_label || pair.task_input}
              reblessed={reblessed || !!g.archived_reason}
              onSourceClick={() => track('demo.evidence_open', { which: 'drift' })}
              onReblessClick={() => track('demo.action_click', { which: 'rebless' })}
              onRebless={
                pair.outcome === 'drift'
                  ? async () => {
                      const res = await rebless.mutateAsync({ golden: pair.golden_run_id, replay: pair.replay_run_id })
                      setReblessed(true)
                      toast({ message: responseToast(res) ?? successToast.rebless(), tone: 'pass' })
                    }
                  : undefined
              }
            />
          ))}
        </section>
      ) : null}
    </div>
  )
}
