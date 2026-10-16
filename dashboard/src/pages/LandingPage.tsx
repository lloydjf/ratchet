import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowRight, Play } from 'lucide-react'
import { isApiError } from '../api/client'
import { useMeta, useScenarios, useSimulate, useVerdict, useVerdicts } from '../api/hooks'
import { useLiveEvent } from '../api/live'
import type { Event, Scenario, VerdictSummary } from '../api/types'
import { markSimulateClick, track } from '../analytics'
import { apiErrorCopy, errorCopy, HERO, LANDING_COPY, LANDING_HEADLINE, replayStep } from '../copy'
import { Button } from '../components/Button'
import { ErrorState } from '../components/ErrorState'
import { StepMark } from '../components/Marks'
import { ScenarioCard, type ScenarioStatus } from '../components/ScenarioCard'
import { Sieve } from '../components/Sieve'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { rememberSession } from '../lib/session'
import { VerdictBand } from './verdict/VerdictHeader'
import { PairsPanel } from './verdict/VerdictEvidence'

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

function recordedFor(s: Pick<Scenario, 'skill'>, verdicts: VerdictSummary[] | undefined): VerdictSummary | null {
  return verdicts?.find((v) => v.recorded && v.affected_skills.includes(s.skill)) ?? null
}

function ScenarioSlot({ scenario, slot, onSimulate, onSettle, recordedHref }: { scenario: Scenario; slot: Slot; onSimulate: () => void; onSettle: (patch: Partial<Slot>) => void; recordedHref: string | null }) {
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

/** Honesty: the replay step names this host's runner. */
function HowItWorks({ runner }: { runner: string | null | undefined }) {
  const steps = ['Bless a task', 'Agent edits a skill', replayStep(runner), 'Level 0 · Nano → Super → Ultra', 'Tavily drift check', 'Block, hold or merge']
  return (
    <ol className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted" aria-label="How a check runs">
      {steps.map((s, i) => (
        <li key={s} className="flex items-center gap-2">
          {i > 0 ? <StepMark /> : null}
          {s === 'Level 0 · Nano → Super → Ultra' ? (
            <span>
              Level 0 · <span className="text-nano-ink">Nano</span> <StepMark /> <span className="text-super-ink">Super</span> <StepMark /> <span className="text-ultra-ink">Ultra</span>
            </span>
          ) : s === 'Tavily drift check' ? (
            <span className="text-drift-ink">{s}</span>
          ) : (
            <span className="text-text">{s}</span>
          )}
        </li>
      ))}
    </ol>
  )
}

/** The hero: a real recorded verdict, read-only, sieve settled. */
function RecordedHero({ verdictId, onRun, running }: { verdictId: string; onRun: () => void; running: boolean }) {
  const q = useVerdict(verdictId)
  const view = q.data
  const v = view?.verdict
  if (q.isLoading)
    return (
      <div className="stack" aria-busy="true" role="status" aria-label="Loading the recorded verdict">
        <div className="flex flex-col gap-3 panel-pad">
          <span className="skeleton block h-14 w-56" />
          <span className="skeleton block h-6 w-3/4" />
        </div>
        <div className="h-[260px] panel-pad">
          <span className="skeleton block h-full w-full" />
        </div>
      </div>
    )
  if (!view || !v) return null
  return (
    <section aria-label="A recorded verdict" className="stack" data-testid="hero">
      <div className="flex flex-col gap-3 px-6 py-3 sm:flex-row sm:items-center sm:justify-between max-md:px-4">
        <p className="text-body text-muted">{HERO.sentence(v.pairs.length, v.affected_skills.join(', '), v.decision)}</p>
        <Button variant="primary" onClick={onRun} disabled={running} data-testid="run-live" className="shrink-0 self-start sm:self-auto">
          <Play size={14} aria-hidden="true" />
          {HERO.run}
        </Button>
      </div>
      <VerdictBand view={view} animate={false} simulatedMeta={false} publicMode readOnly testId="hero-band" />
      <Sieve events={[]} pairs={v.pairs} verdictId={v.verdict_id} frozen />
      <PairsPanel view={view} verdictId={v.verdict_id} only={null} onClearFilter={() => {}} selected={null} limit={4} />
      <div className="px-6 py-3 max-md:px-4">
        <Link to={`/verdicts/${encodeURIComponent(v.verdict_id)}`} className="link inline-flex min-h-8 items-center gap-1 text-body">
          {HERO.openFull} <ArrowRight size={14} aria-hidden="true" />
        </Link>
      </div>
    </section>
  )
}

export default function LandingPage() {
  useDocumentTitle([])
  const scenarios = useScenarios()
  const { data: meta } = useMeta()
  const { data: verdicts } = useVerdicts()
  const simulate = useSimulate()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
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

  const run = useCallback(
    async (s: Scenario): Promise<string | null> => {
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
        return res.status === 'queued' ? null : res.verdict_id
      } catch (e) {
        update(s.scenario_id, { status: 'failed', error: apiErrorCopy(isApiError(e) ? e : null) })
        return null
      }
    },
    [simulate, update],
  )

  // The command palette's "Simulate <skill>" lands here with ?run=<scenario>.
  const ran = useRef<string | null>(null)
  const wanted = params.get('run')
  useEffect(() => {
    if (!wanted || ran.current === wanted || !scenarios.data) return
    const s = scenarios.data.find((x) => x.scenario_id === wanted)
    const t = setTimeout(() => {
      ran.current = wanted
      const next = new URLSearchParams(params)
      next.delete('run')
      setParams(next, { replace: true })
      if (s) void run(s)
    }, 0)
    return () => clearTimeout(t)
  }, [wanted, scenarios.data, params, setParams, run])

  const simulateOff = meta ? !meta.simulate_enabled : false
  const list = scenarios.data ?? []
  const heroScenario = list.find((s) => recordedFor(s, verdicts?.items)?.decision === 'BLOCK') ?? list[0]
  const hero = heroScenario ? recordedFor(heroScenario, verdicts?.items) : null
  const heroSlot = heroScenario ? slots[heroScenario.scenario_id] : undefined

  const runLive = async () => {
    if (!heroScenario) return
    const id = await run(heroScenario)
    if (id) navigate(`/verdicts/${encodeURIComponent(id)}`)
  }

  return (
    <div className="flex flex-col gap-10">
      <header className="flex max-w-[60ch] flex-col gap-2 pt-2">
        <h1 className="text-h1 font-semibold text-text">{LANDING_HEADLINE}</h1>
      </header>

      {hero ? <RecordedHero verdictId={hero.verdict_id} onRun={() => void runLive()} running={simulateOff || heroSlot?.status === 'running' || heroSlot?.status === 'queued'} /> : null}

      <section aria-labelledby="scenarios-title" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="scenarios-title" className="text-h3 font-semibold text-text">
            {HERO.scenariosTitle}
          </h2>
          <p className="text-meta text-muted">{HERO.scenariosNote}</p>
        </div>
        {scenarios.isError ? (
          <ErrorState error={scenarios.error} onRetry={() => void scenarios.refetch()} />
        ) : (
          <ol className="grid grid-cols-1 gap-px border border-border bg-border md:grid-cols-3">
            {list.map((s) => (
              <li key={s.scenario_id} className="min-w-0 bg-surface">
                <ScenarioSlot scenario={s} slot={slots[s.scenario_id] ?? EMPTY_SLOT} onSimulate={() => void run(s)} onSettle={(p) => update(s.scenario_id, p)} recordedHref={recordedFor(s, verdicts?.items) ? `/verdicts/${encodeURIComponent(recordedFor(s, verdicts?.items)!.verdict_id)}` : null} />
              </li>
            ))}
            {scenarios.isLoading
              ? Array.from({ length: 3 }, (_, i) => (
                  <li key={i} className="flex h-64 flex-col gap-2 bg-surface p-5" aria-hidden="true">
                    <span className="skeleton block h-5 w-40" />
                    <span className="skeleton mt-4 block h-3.5 w-full" />
                    <span className="skeleton block h-3.5 w-4/5" />
                    <span className="skeleton mt-4 block h-3.5 w-full" />
                    <span className="skeleton block h-3.5 w-3/5" />
                  </li>
                ))
              : null}
          </ol>
        )}
        {simulateOff ? <p className="text-meta text-warn-ink">{errorCopy('simulate_disabled')}</p> : null}
      </section>

      <section className="flex flex-col gap-3 border-t border-border pt-6" aria-label="How Ratchet works">
        <HowItWorks runner={meta?.runner} />
        <p className="max-w-prose text-body text-muted" data-testid="landing-copy">
          {LANDING_COPY}
        </p>
        <div className="flex flex-wrap gap-x-5">
          <Link to="/timeline" className="link inline-flex min-h-8 items-center gap-1 text-body">
            Open the timeline <ArrowRight size={14} aria-hidden="true" />
          </Link>
          {meta && !meta.public_mode ? (
            <Link to="/traces" className="link inline-flex min-h-8 items-center gap-1 text-body">
              Golden library <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : null}
        </div>
      </section>
    </div>
  )
}
