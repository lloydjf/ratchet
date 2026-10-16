import { Link } from 'react-router-dom'
import { ArrowRight, Play } from 'lucide-react'
import type { Decision, Progress, Scenario } from '../api/types'
import { RETRY, SCENARIO, VIEW_RECORDED } from '../copy'
import { Button } from './Button'
import { DecisionChip } from './DecisionChip'
import { ProgressStrip } from './ProgressStrip'

export type ScenarioStatus = 'idle' | 'running' | 'queued' | 'done' | 'failed'

interface ScenarioCardProps {
  scenario: Scenario
  /** Kept for callers that number their scenarios; not shown (no decorative numbering). */
  index?: number
  status: ScenarioStatus
  verdictId?: string | null
  decision?: Decision | null
  progress?: Progress | null
  queue?: { position: number; etaS: number } | null
  failedMessage?: string
  recordedHref?: string | null
  onSimulate: () => void
  onOpen?: () => void
  disabled?: boolean
}

const secondaryLink = 'inline-flex min-h-9 items-center justify-center gap-2 rounded-sm border border-border-strong bg-surface px-3.5 text-body text-text hover:bg-surface-2 max-md:min-h-11'

/** One seeded scenario on the landing page: idle → running (a slim stage line) → done (or queued / failed). */
export function ScenarioCard({ scenario, status, verdictId, decision, progress, queue, failedMessage, recordedHref, onSimulate, onOpen, disabled }: ScenarioCardProps) {
  const titleId = `scenario-${scenario.scenario_id}`
  const shownDecision = decision ?? scenario.expected_decision
  return (
    <article aria-labelledby={titleId} className="flex h-full flex-col bg-surface p-5 max-md:p-4" data-testid="scenario-card" data-scenario={scenario.scenario_id} data-status={status}>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={titleId} className="font-mono text-h3 font-medium break-words text-text">
            {scenario.skill}
          </h3>
          {scenario.title && scenario.title !== scenario.skill ? <p className="mt-0.5 text-meta text-muted">{scenario.title}</p> : null}
        </div>
        <DecisionChip decision={shownDecision} size="sm" muted={status !== 'done'} animate={status === 'done'} />
      </header>

      {status === 'running' && progress ? (
        <div className="mt-4 flex flex-1 flex-col">
          <ProgressStrip compact stage={progress.stage} stageLabel={progress.stage_label} k={progress.k} n={progress.n} elapsedS={progress.elapsed_s} usdByTier={progress.usd_by_tier} stages={progress.stages} stalled={progress.stalled} />
          {verdictId ? (
            <Link to={`/verdicts/${encodeURIComponent(verdictId)}`} className="link mt-3 inline-flex min-h-8 items-center gap-1 self-start text-body" onClick={onOpen}>
              Watch it on the verdict page <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : null}
        </div>
      ) : (
        <dl className="mt-4 flex flex-1 flex-col gap-3">
          <div>
            <dt className="text-meta text-muted">{SCENARIO.learned}</dt>
            <dd className="mt-0.5 text-body text-text">{scenario.learned}</dd>
          </div>
          <div>
            <dt className="text-meta text-muted">{SCENARIO.catches}</dt>
            <dd className="mt-0.5 text-body text-text">{scenario.catches}</dd>
          </div>
        </dl>
      )}

      {status === 'queued' && queue ? (
        <p className="mt-4 border border-border bg-bg px-3 py-2 text-meta text-text" role="status">
          {SCENARIO.queued(queue.position, queue.etaS)}
        </p>
      ) : null}
      {status === 'failed' ? (
        <p className="mt-4 border tint-warn px-3 py-2 text-meta" role="alert">
          {failedMessage ?? SCENARIO.failed}
        </p>
      ) : null}

      <footer className="mt-5 flex flex-wrap items-center gap-2">
        {status === 'done' && verdictId ? (
          <Link to={`/verdicts/${encodeURIComponent(verdictId)}`} onClick={onOpen} className={secondaryLink} data-testid="open-verdict">
            {SCENARIO.openVerdict} <ArrowRight size={14} aria-hidden="true" />
          </Link>
        ) : status === 'failed' ? (
          <>
            <Button onClick={onSimulate}>{RETRY}</Button>
            {recordedHref ? (
              <Link to={recordedHref} className={secondaryLink}>
                {VIEW_RECORDED}
              </Link>
            ) : null}
          </>
        ) : (
          <Button onClick={onSimulate} disabled={disabled || status === 'running' || status === 'queued'} data-testid="simulate">
            <Play size={14} aria-hidden="true" />
            {status === 'running' ? `${SCENARIO.running}…` : status === 'queued' ? 'Queued…' : SCENARIO.simulate}
            <span className="sr-only"> {scenario.skill}</span>
          </Button>
        )}
      </footer>
    </article>
  )
}
