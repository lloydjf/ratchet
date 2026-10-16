import { useEffect, useState, type ReactNode } from 'react'
import type { Decision, PairOutcome } from '../api/types'
import { BADGE, EMPTY, TRUNCATED_MARKER } from '../copy'
import { ActionButton } from '../components/ActionButton'
import { Badge } from '../components/Badge'
import { CostPanel } from '../components/CostPanel'
import { CostTicker } from '../components/CostTicker'
import { DecisionChip } from '../components/DecisionChip'
import { DiffViewer } from '../components/DiffViewer'
import { DriftCard } from '../components/DriftCard'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { JudgeTrail } from '../components/JudgeTrail'
import { OpinionCard } from '../components/OpinionCard'
import { OutcomeChip } from '../components/OutcomeChip'
import { PairGrid } from '../components/PairGrid'
import { ProgressStrip } from '../components/ProgressStrip'
import { ScenarioCard } from '../components/ScenarioCard'
import { StepList } from '../components/StepList'
import { TierBadge } from '../components/TierBadge'
import { TimelineItem } from '../components/TimelineItem'
import { Toast, useToast } from '../components/Toast'
import { Button } from '../components/Button'
import { ApiError } from '../api/client'
import { alignSteps } from '../lib/align'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useTheme } from '../lib/theme'
import { DIFF, DRIFT_CHANGED, DRIFT_UNAVAILABLE, DRIFT_UNCHANGED, EVENTS, PAIRS, SCENARIO, SIEVE_STATES, STEPS_GOLDEN, STEPS_REPLAY, op, progress, sampleStream, sampleView } from '../kit/samples'
import { Sieve, SieveThumb } from '../components/Sieve'
import { TrailGlyph } from '../components/TrailGlyph'
import { VerdictBand } from './verdict/VerdictHeader'
import { gitAction } from '../copy'

function Group({ id, title, children, note }: { id: string; title: string; children: ReactNode; note?: string }) {
  return (
    <section aria-labelledby={`kit-${id}`} className="flex flex-col gap-3 border-t border-border pt-6" data-kit={id}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={`kit-${id}`} className="text-h3 font-semibold text-text">
          {title}
        </h2>
        {note ? <p className="text-meta text-muted">{note}</p> : null}
      </div>
      {children}
    </section>
  )
}

const Row = ({ children }: { children: ReactNode }) => <div className="flex flex-wrap items-center gap-3">{children}</div>
const noop = async () => new Promise((r) => setTimeout(r, 400))

export default function KitPage() {
  useDocumentTitle(['Kit'])
  const [theme, toggle] = useTheme()
  const [grid, setGrid] = useState(false)
  const toast = useToast()
  const rows = alignSteps(STEPS_GOLDEN, STEPS_REPLAY)
  const decisions: Decision[] = ['BLOCK', 'WARN', 'PASS']
  const outcomes: PairOutcome[] = ['regression', 'uncertain', 'drift', 'improved', 'pass', 'failed']
  return (
    <div className="flex flex-col gap-8" data-testid="kit">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-h1 font-semibold text-text">Component kit</h1>
          <p className="mt-1 text-body text-muted">Every design-system component in every state, for rehearsal screenshots and visual checks.</p>
          <p className="mt-1 font-mono text-meta text-muted">/__kit</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => setGrid((g) => !g)} aria-pressed={grid} data-testid="toggle-grid">
            {grid ? 'Hide the 8-px grid' : 'Show the 8-px grid'}
          </Button>
          <Button onClick={toggle}>Switch to {theme === 'dark' ? 'light' : 'dark'} theme</Button>
        </div>
      </header>
      {grid ? <ConstructionGrid /> : null}

      <Group id="sieve" title="Cascade sieve" note="every outcome state, settled · and the live stream">
        <div className="stack">
          <LiveSieve />
          {SIEVE_STATES.map((s) => (
            <Sieve key={s.outcome} title={s.title} events={[]} pairs={s.pairs} verdictId="v_kit" frozen />
          ))}
        </div>
        <Row>
          {SIEVE_STATES.map((s) => (
            <span key={s.outcome} className="border border-border bg-surface p-2">
              <SieveThumb pairs={s.pairs} />
            </span>
          ))}
        </Row>
      </Group>

      <Group id="band" title="Decision band" note="Blocked · Held · Passed">
        <div className="stack">
          {(['BLOCK', 'WARN', 'PASS'] as const).map((d) => (
            <VerdictBand key={d} view={sampleView(d, SIEVE_STATES[0]!.pairs)} animate={false} simulatedMeta={false} publicMode readOnly testId={`kit-band-${d}`} />
          ))}
        </div>
      </Group>

      <Group id="decision" title="DecisionChip" note="sm · md · lg · muted (expected)">
        {(['sm', 'md', 'lg'] as const).map((s) => (
          <Row key={s}>
            {decisions.map((d) => (
              <DecisionChip key={d} decision={d} size={s} />
            ))}
            {decisions.map((d) => (
              <DecisionChip key={`m${d}`} decision={d} size={s} muted />
            ))}
          </Row>
        ))}
      </Group>

      <Group id="outcome" title="OutcomeChip">
        <Row>
          {outcomes.map((o) => (
            <OutcomeChip key={o} outcome={o} />
          ))}
        </Row>
        <Row>
          {outcomes.map((o) => (
            <OutcomeChip key={o} outcome={o} size="sm" />
          ))}
        </Row>
      </Group>

      <Group id="tier" title="TierBadge" note="plain · with verdict (hover or focus for the tooltip) · skipped">
        <Row>
          <TierBadge tier="nano" plain />
          <TierBadge tier="super" plain />
          <TierBadge tier="ultra" plain />
          <TierBadge tier="nano" verdict="equivalent" confidence={0.94} reasons={['Same labels on the same issues.']} />
          <TierBadge tier="super" verdict="regressed" confidence={0.86} reasons={['sheets.append rows lost the vat field.']} />
          <TierBadge tier="ultra" verdict="uncertain" confidence={0.55} reasons={['Intent unclear.']} />
          <TierBadge tier="ultra" verdict="improved" confidence={0.88} />
          <TierBadge tier="super" skipped />
          <TierBadge tier="ultra" skipped />
        </Row>
      </Group>

      <Group id="trail" title="JudgeTrail" note="cascade order; dotted = not needed">
        <div className="flex flex-col gap-2">
          {PAIRS.slice(0, 5).map((p) => (
            <Row key={p.golden_run_id}>
              <span className="w-56 text-meta text-muted">{p.task_label}</span>
              <TrailGlyph p={p} />
              <JudgeTrail opinions={p.opinions} decidedBy={p.decided_by} drift={p.drift} />
            </Row>
          ))}
        </div>
      </Group>

      <Group id="ticker" title="CostTicker" note="idle · live">
        <Row>
          <CostTicker usdByTier={{ nano: 0.0049, super: 0.0118, ultra: 0.0214 }} live={false} />
          <CostTicker usdByTier={{ nano: 0.0049, super: 0.0118, ultra: 0.0214 }} live />
          <CostTicker usdByTier={{}} live={false} />
        </Row>
      </Group>

      <Group id="costpanel" title="CostPanel">
        <div className="grid gap-3 md:grid-cols-2">
          <CostPanel usdByTier={{ nano: 0.0059, super: 0.0123, ultra: 0.0574 }} tokensByTier={{ nano: 98_600, super: 40_080, ultra: 59_860 }} counterfactualUsd={0.2615} />
          <CostPanel usdByTier={{ nano: 0.0048, super: 0, ultra: 0.0063 }} tokensByTier={{ nano: 65_760, super: 0, ultra: 5_560 }} counterfactualUsd={0.1394} />
        </div>
      </Group>

      <Group id="progress" title="ProgressStrip" note="running · stalled · done · failed · compact">
        <ProgressStrip {...stripProps('replaying', 9)} />
        <ProgressStrip {...stripProps('nano')} stalled />
        <ProgressStrip {...stripProps('done')} status="done" />
        <ProgressStrip {...stripProps('replaying', 4)} status="failed" failedMessage="Replays failed on 3 of 14 tasks, so Ratchet can't decide. The edit is held." />
        <div className="max-w-sm">
          <ProgressStrip {...stripProps('replaying', 9)} compact />
        </div>
      </Group>

      <Group id="diff" title="DiffViewer" note="expanded · collapsed by default">
        <DiffViewer patch={DIFF} />
        <DiffViewer patch={DIFF} collapsedByDefault />
      </Group>

      <Group id="pairgrid" title="PairGrid" note="loaded (table) · cards (mobile) · loading · empty">
        <div className="border border-border bg-surface">
          <PairGrid pairs={PAIRS} verdictId="v_kit" forceLayout="table" />
        </div>
        <div className="max-w-md border border-border bg-surface">
          <PairGrid pairs={PAIRS.slice(1, 4)} verdictId="v_kit" forceLayout="cards" />
        </div>
        <div className="border border-border bg-surface">
          <PairGrid pairs={[]} verdictId="v_kit" loading />
        </div>
        <PairGrid pairs={[]} verdictId="v_kit" />
      </Group>

      <Group id="opinion" title="OpinionCard" note="normal · truncated">
        <div className="grid gap-3 lg:grid-cols-2">
          <OpinionCard opinion={op('super', 'regressed', 0.86, 'sheets.append rows lost the vat field, so the total equals the subtotal.', { criteria_met: [{ criterion: 'The VAT line for each receipt that has one is listed.', met: false, evidence_steps: ['t007'] }, { criterion: 'Every receipt line item appears in the report.', met: true, evidence_steps: [] }] })} onStepClick={() => {}} />
          <OpinionCard opinion={op('ultra', 'uncertain', 0.55, 'Grouping by type is weaker but every item is present.', { truncated: true, causal_explanation: 'Step 2 renames the Fixes section.' })} />
        </div>
        <p className="text-meta text-muted">Truncated marker: {TRUNCATED_MARKER}</p>
      </Group>

      <Group id="drift" title="DriftCard" note="pending · changed · unchanged · unavailable">
        <div className="grid gap-3 lg:grid-cols-2">
          <DriftCard check={null} />
          <DriftCard check={DRIFT_CHANGED} task="Berlin trip to USD" onRebless={noop} />
          <DriftCard check={DRIFT_UNCHANGED} />
          <DriftCard check={DRIFT_UNAVAILABLE} />
        </div>
      </Group>

      <Group id="steps" title="StepList" note="golden · replay, aligned; differing steps highlighted">
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-card border border-border">
            <StepList rows={rows.map((r) => ({ step: r.golden, differs: r.differs }))} side="golden" />
          </div>
          <div className="rounded-card border border-border">
            <StepList rows={rows.map((r) => ({ step: r.replay, differs: r.differs }))} side="replay" focusStep="t006" />
          </div>
        </div>
      </Group>

      <Group id="timeline" title="TimelineItem" note="one per kind">
        <ol className="divide-y divide-border border border-border">
          {EVENTS.map((e) => (
            <TimelineItem key={e.id} event={e} />
          ))}
        </ol>
      </Group>

      <Group id="scenario" title="ScenarioCard" note="idle · running · queued · done · failed">
        <div className="grid gap-3 md:grid-cols-3">
          <ScenarioCard scenario={SCENARIO} index={1} status="idle" onSimulate={() => {}} />
          <ScenarioCard scenario={SCENARIO} index={1} status="running" verdictId="v_kit" progress={progress('replaying', 9)} onSimulate={() => {}} />
          <ScenarioCard scenario={SCENARIO} index={1} status="queued" queue={{ position: 5, etaS: 20 }} onSimulate={() => {}} />
          <ScenarioCard scenario={SCENARIO} index={1} status="done" verdictId="v_kit" decision="BLOCK" onSimulate={() => {}} />
          <ScenarioCard scenario={SCENARIO} index={1} status="failed" recordedHref="/verdicts/v_rec_gt" onSimulate={() => {}} />
        </div>
      </Group>

      <Group id="actions" title="ActionButton" note="opens the confirm dialog with the exact confirm body">
        <Row>
          <ActionButton kind="rollback" subject="8b41e07" onConfirm={noop} />
          <ActionButton kind="approve" subject="release-notes" onConfirm={noop} />
          <ActionButton kind="approveAnyway" subject="github-triage" onConfirm={noop} />
          <ActionButton kind="unbless" subject="Triage the acme/widgets backlog" onConfirm={noop} />
          <ActionButton kind="rebless" subject="Berlin trip to USD" onConfirm={noop} />
          <ActionButton kind="rebless" subject="Berlin trip to USD" onConfirm={noop} done doneLabel="Re-blessed" />
        </Row>
        <Row>
          <ActionButton kind="rollback" subject="8b41e07" git={gitAction.rollback('8b41e07', false)} onConfirm={noop} defaultOpen />
          <ActionButton kind="approveAnyway" subject="github-triage" git={gitAction.approveAnyway('c0ffee1')} onConfirm={noop} defaultOpen />
        </Row>
      </Group>

      <Group id="badge" title="Badge">
        <Row>
          <Badge text={BADGE.recorded} hint={BADGE.recordedHint} />
          <Badge text={BADGE.truncated} tone="warn" hint={TRUNCATED_MARKER} />
          <Badge text={BADGE.driftUnavailable} tone="warn" />
          <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} />
          <Badge text={BADGE.worldDependent} tone="drift" />
          <Badge text="Hard fail" tone="block" />
          <Badge text="Changed args" />
        </Row>
      </Group>

      <Group id="empty" title="EmptyState · ErrorState">
        <EmptyState title={EMPTY.timeline} action={{ label: 'Simulate a scenario', to: '/' }} />
        <EmptyState title={EMPTY.costs} />
        <ErrorState error={new ApiError('budget_exceeded', 'cap', 429, 'req_7f3a2c')} onRetry={() => {}} />
        <ErrorState error={new ApiError('upstream_unavailable', 'down', 502)} onRetry={() => {}} />
      </Group>

      <Group id="toast" title="Toast" note="6 s, dismissible, stacks to 3">
        <ol className="flex max-w-[420px] flex-col gap-2">
          <Toast item={{ id: -1, message: 'Rolled back. main is unchanged.', tone: 'neutral', link: { label: 'Open the edit', to: '/edits/e_rec_gt' } }} onDismiss={() => {}} />
          <Toast item={{ id: -2, message: 'Merged. release-notes is live.', tone: 'pass' }} onDismiss={() => {}} />
          <Toast item={{ id: -3, message: 'Spending cap reached for today.', tone: 'warn' }} onDismiss={() => {}} />
        </ol>
        <Row>
          <Button onClick={() => toast({ message: "Re-blessed. Future checks compare against today's result.", tone: 'pass' })}>Show a toast</Button>
        </Row>
      </Group>
    </div>
  )
}

function stripProps(stage: string, k: number | null = null) {
  const p = progress(stage, k)
  return { stage: p.stage, stageLabel: p.stage_label, k: p.k, n: p.n, elapsedS: p.elapsed_s, usdByTier: p.usd_by_tier, stages: p.stages, tickLocally: false }
}

/** The 8-px construction grid as a real overlay state. */
function ConstructionGrid() {
  return (
    <svg className="pointer-events-none fixed inset-0 z-[90] h-full w-full" aria-hidden="true" data-testid="construction-grid">
      <defs>
        <pattern id="kit-grid-8" width="8" height="8" patternUnits="userSpaceOnUse">
          <path d="M8 0H0V8" fill="none" stroke="var(--accent)" strokeOpacity="0.18" strokeWidth="1" />
        </pattern>
        <pattern id="kit-grid-64" width="64" height="64" patternUnits="userSpaceOnUse">
          <path d="M64 0H0V64" fill="none" stroke="var(--accent)" strokeOpacity="0.35" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#kit-grid-8)" />
      <rect width="100%" height="100%" fill="url(#kit-grid-64)" />
    </svg>
  )
}

const LIVE_STREAM = sampleStream(SIEVE_STATES[0]!.pairs)

/** The sieve fed one event every 140 ms, as a run streams in. */
function LiveSieve() {
  const [n, setN] = useState(LIVE_STREAM.length)
  const playing = n < LIVE_STREAM.length
  useEffect(() => {
    if (!playing) return
    const t = setTimeout(() => setN((x) => x + 1), 140)
    return () => clearTimeout(t)
  }, [n, playing])
  return (
    <div className="flex flex-col">
      <Sieve title="Live stream (sample)" events={LIVE_STREAM.slice(0, n)} pairs={[]} verdictId="v_kit_live" />
      <div className="px-6 pb-4">
        <Button size="sm" onClick={() => setN(0)} disabled={playing} data-testid="replay-stream">
          Replay the stream
        </Button>
      </div>
    </div>
  )
}
