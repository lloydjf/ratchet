import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight } from 'lucide-react'
import { isApiError } from '../api/client'
import { useMeta, useScenarios, useSimulate, useVerdict, useVerdicts } from '../api/hooks'
import { useLiveEvent } from '../api/live'
import type { Event, Scenario, VerdictSummary } from '../api/types'
import { markSimulateClick, track } from '../analytics'
import { apiErrorCopy, errorCopy, LANDING_COPY, LANDING_HEADLINE, replayStep } from '../copy'
import { ScenarioCard, type ScenarioStatus } from '../components/ScenarioCard'
import { ErrorState } from '../components/ErrorState'
import { HeaderTicker } from '../layout/AppShell'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { rememberSession } from '../lib/session'

interface Slot {
  verdictId: string | null
  status: ScenarioStatus
  queue: { position: number; etaS: number } | null
  error: string | null
}

const KEY = 'ratchet.simulate.slots'

function loadSlots(): Record<string, Slot> {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as Record<string, Slot>
  } catch {
    return {}
  }
}
function saveSlots(s: Record<string, Slot>) {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(s))
  } catch {
    /* storage unavailable: state lasts for this page view */
  }
}

const EMPTY_SLOT: Slot = { verdictId: null, status: 'idle', queue: null, error: null }

function recordedFor(s: Scenario, verdicts: VerdictSummary[] | undefined): string | null {
  const hit = verdicts?.find((v) => v.recorded && v.affected_skills.includes(s.skill))
  return hit ? `/verdicts/${encodeURIComponent(hit.verdict_id)}` : null
}

function ScenarioSlot({
  scenario,
  index,
  slot,
  onSimulate,
  onSettle,
  recordedHref,
}: {
  scenario: Scenario
  index: number
  slot: Slot
  onSimulate: () => void
  onSettle: (patch: Partial<Slot>) => void
  recordedHref: string | null
}) {
  const q = useVerdict(slot.verdictId ?? undefined)
  const view = q.data
  const decided = view?.verdict ?? null
  let status: ScenarioStatus = slot.status
  if (slot.verdictId && status !== 'failed') {
    if (decided) status = 'done'
    else if (view?.state === 'queued' || (!view && slot.status === 'queued')) status = 'queued'
    else if (view?.state === 'failed_infra') status = 'failed'
    else status = 'running'
  }
  if (q.isError && slot.verdictId && status !== 'failed') status = 'failed'

  // Persist terminal states so a judge returning to the landing page sees them.
  useEffect(() => {
    if ((status === 'done' || status === 'failed') && slot.status !== status) onSettle({ status })
  }, [status, slot.status, onSettle])

  return (
    <ScenarioCard
      scenario={scenario}
      index={index}
      status={status}
      verdictId={slot.verdictId}
      decision={decided?.decision ?? null}
      progress={view?.progress ?? null}
      queue={slot.queue}
      failedMessage={slot.error ?? (q.isError ? apiErrorCopy(isApiError(q.error) ? q.error : null) : undefined)}
      recordedHref={recordedHref}
      onSimulate={onSimulate}
    />
  )
}

/** Honesty: the replay step names this host's runner, like the progress strip does. */
function loop(runner: string | null | undefined): { label: string; tone?: string }[] {
  return [
    { label: 'Bless a task' },
    { label: 'Agent edits a skill' },
    { label: replayStep(runner) },
    { label: 'Level 0 · Nano → Super → Ultra', tone: 'tiers' },
    { label: 'Tavily drift check', tone: 'drift' },
    { label: 'Block, hold or merge' },
  ]
}

function HowItWorks({ runner }: { runner: string | null | undefined }) {
  const LOOP = loop(runner)
  return (
    <ol className="flex flex-wrap items-center gap-2 font-mono text-mono text-muted" aria-label="How a check runs">
      {LOOP.map((s, i) => (
        <li key={s.label} className="flex items-center gap-1.5">
          <span className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-border bg-surface px-2 text-text">
            <span className="num text-muted">{i + 1}</span>
            {s.tone === 'tiers' ? (
              <span>
                Level 0 · <span className="text-nano-ink">Nano</span> → <span className="text-super-ink">Super</span> → <span className="text-ultra-ink">Ultra</span>
              </span>
            ) : s.tone === 'drift' ? (
              <span className="text-drift-ink">{s.label}</span>
            ) : (
              s.label
            )}
          </span>
        </li>
      ))}
    </ol>
  )
}

export default function LandingPage() {
  useDocumentTitle([])
  const scenarios = useScenarios()
  const { data: meta } = useMeta()
  const { data: verdicts } = useVerdicts()
  const simulate = useSimulate()
  const [slots, setSlots] = useState<Record<string, Slot>>(loadSlots)

  useEffect(() => {
    track('demo.landing_view')
  }, [])

  const update = useCallback((id: string, patch: Partial<Slot>) => {
    setSlots((prev) => {
      const next = { ...prev, [id]: { ...(prev[id] ?? EMPTY_SLOT), ...patch } }
      saveSlots(next)
      return next
    })
  }, [])

  // simulate.finished with a failure status flips the card to failed.
  useLiveEvent((e: Event) => {
    const scenario = typeof e.payload.scenario === 'string' ? e.payload.scenario : null
    if (!scenario) return
    const vid = typeof e.payload.verdict_id === 'string' ? e.payload.verdict_id : null
    // A queued run may only learn its verdict id when it starts.
    if (e.kind === 'simulate.started' && vid) {
      setSlots((prev) => {
        const cur = prev[scenario]
        if (!cur || cur.verdictId || cur.status === 'idle') return prev
        const next = { ...prev, [scenario]: { ...cur, verdictId: vid, status: 'running' as const, queue: null } }
        saveSlots(next)
        return next
      })
      markSimulateClick(scenario, vid)
    }
    if (e.kind === 'simulate.finished' && e.payload.status === 'failed') update(scenario, { status: 'failed' })
  })

  const run = async (s: Scenario) => {
    track('demo.simulate_click', { scenario: s.scenario_id })
    update(s.scenario_id, { status: 'running', verdictId: null, queue: null, error: null })
    try {
      const res = await simulate.mutateAsync(s.scenario_id)
      rememberSession(res.session_hash)
      if (res.verdict_id) markSimulateClick(s.scenario_id, res.verdict_id)
      update(s.scenario_id, {
        verdictId: res.verdict_id,
        status: res.status === 'queued' ? 'queued' : res.status === 'done' ? 'done' : 'running',
        queue: res.status === 'queued' ? { position: res.queue_position ?? 1, etaS: res.eta_s ?? 20 } : null,
      })
    } catch (e) {
      update(s.scenario_id, { status: 'failed', error: apiErrorCopy(isApiError(e) ? e : null) })
    }
  }

  const simulateOff = meta ? !meta.simulate_enabled : false

  return (
    <div className="mx-auto flex max-w-landing flex-col gap-10 sm:gap-12">
      <section className="flex flex-col gap-5 pt-2 sm:pt-8">
        <p className="eyebrow">Live demo · no login, nothing to install</p>
        <h1 className="text-h1 font-semibold text-text">{LANDING_HEADLINE}</h1>
        <div className="sm:hidden">
          <HeaderTicker />
        </div>
        <p className="max-w-prose text-narrative text-muted" data-testid="landing-copy">
          {LANDING_COPY}
        </p>
        <HowItWorks runner={meta?.runner} />
      </section>

      <section aria-labelledby="scenarios-title" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="scenarios-title" className="text-h3 font-semibold text-text">
            Three skill edits to try
          </h2>
          <p className="text-meta text-muted">Each run takes under a minute and is yours alone.</p>
        </div>
        {scenarios.isError ? (
          <ErrorState error={scenarios.error} onRetry={() => void scenarios.refetch()} />
        ) : (
          <ol className="grid grid-cols-1 gap-3 md:grid-cols-3">
            {(scenarios.data ?? []).map((s, i) => (
              <li key={s.scenario_id} className="min-w-0">
                <ScenarioSlot
                  scenario={s}
                  index={i + 1}
                  slot={slots[s.scenario_id] ?? EMPTY_SLOT}
                  onSimulate={() => void run(s)}
                  onSettle={(p) => update(s.scenario_id, p)}
                  recordedHref={recordedFor(s, verdicts?.items)}
                />
              </li>
            ))}
            {scenarios.isLoading
              ? Array.from({ length: 3 }, (_, i) => (
                  <li key={i} className="h-72 rounded-card border border-border bg-surface p-4" aria-hidden="true">
                    <span className="skeleton block h-4 w-20" />
                    <span className="skeleton mt-2 block h-6 w-40" />
                    <span className="skeleton mt-6 block h-4 w-full" />
                    <span className="skeleton mt-2 block h-4 w-4/5" />
                  </li>
                ))
              : null}
          </ol>
        )}
        {simulateOff ? <p className="text-meta text-warn-ink">{errorCopy('simulate_disabled')}</p> : null}
      </section>

      <section className="flex flex-col gap-3 border-t border-border pt-6 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-meta text-muted">Every verdict, edit and blessing for the demo agent is on the timeline.</p>
        <div className="flex flex-wrap gap-x-5">
          <Link to="/timeline" className="link inline-flex min-h-11 items-center gap-1 text-meta font-medium">
            Open the timeline <ArrowRight size={14} aria-hidden="true" />
          </Link>
          {meta && !meta.public_mode ? (
            <Link to="/traces" className="link inline-flex min-h-11 items-center gap-1 text-meta font-medium">
              Golden library <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : null}
        </div>
      </section>
    </div>
  )
}
