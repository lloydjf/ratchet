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
  index: number
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

/** One seeded scenario on the landing page: idle → running → done (or queued / failed). */
export function ScenarioCard({
  scenario,
  index,
  status,
  verdictId,
  decision,
  progress,
  queue,
  failedMessage,
  recordedHref,
  onSimulate,
  onOpen,
  disabled,
}: ScenarioCardProps) {
  const titleId = `scenario-${scenario.scenario_id}`
  const shownDecision = decision ?? scenario.expected_decision
  return (
    <article
      aria-labelledby={titleId}
      className={`flex h-full flex-col rounded-card border bg-surface p-4 transition-colors duration-[160ms] ${status === 'running' ? 'border-border-strong' : 'border-border'}`}
      data-testid="scenario-card"
      data-scenario={scenario.scenario_id}
      data-status={status}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="eyebrow num">Scenario {index}</p>
          <h3 id={titleId} className="mt-1 font-mono text-h3 font-semibold break-words text-text">
            {scenario.skill}
          </h3>
          {scenario.title && scenario.title !== scenario.skill ? <p className="mt-0.5 text-meta text-muted">{scenario.title}</p> : null}
        </div>
        <DecisionChip decision={shownDecision} size="sm" muted={status !== 'done'} animate={status === 'done'} />
      </header>

      {status === 'running' && progress ? (
        <div className="mt-4 flex flex-1 flex-col">
          <ProgressStrip
            compact
            stage={progress.stage}
            stageLabel={progress.stage_label}
            k={progress.k}
            n={progress.n}
            elapsedS={progress.elapsed_s}
            usdByTier={progress.usd_by_tier}
            stages={progress.stages}
            stalled={progress.stalled}
            className="border-border bg-bg"
          />
          {verdictId ? (
            <Link to={`/verdicts/${encodeURIComponent(verdictId)}`} className="link mt-3 inline-flex min-h-11 items-center gap-1 self-start text-meta font-medium" onClick={onOpen}>
              Watch it on the verdict page <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : null}
        </div>
      ) : (
        <dl className="mt-4 flex flex-1 flex-col gap-3">
          <div>
            <dt className="text-mono text-muted">{SCENARIO.learned}</dt>
            <dd className="mt-0.5 text-body text-text">{scenario.learned}</dd>
          </div>
          <div>
            <dt className="text-mono text-muted">{SCENARIO.catches}</dt>
            <dd className="mt-0.5 text-body text-text">{scenario.catches}</dd>
          </div>
        </dl>
      )}

      {status === 'queued' && queue ? (
        <p className="mt-4 rounded-sm border border-border bg-bg px-3 py-2 text-meta text-text" role="status">
          {SCENARIO.queued(queue.position, queue.etaS)}
        </p>
      ) : null}
      {status === 'failed' ? (
        <p className="mt-4 rounded-sm border tint-warn px-3 py-2 text-meta" role="alert">
          {failedMessage ?? SCENARIO.failed}
        </p>
      ) : null}

      <footer className="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-4">
        {status === 'done' && verdictId ? (
          <Link
            to={`/verdicts/${encodeURIComponent(verdictId)}`}
            onClick={onOpen}
            className="inline-flex min-h-11 flex-1 items-center justify-center gap-2 rounded-sm border border-accent bg-accent px-4 text-body font-semibold text-bg hover:brightness-110"
            data-testid="open-verdict"
          >
            {SCENARIO.openVerdict} <ArrowRight size={16} aria-hidden="true" />
          </Link>
        ) : status === 'failed' ? (
          <>
            <Button variant="primary" onClick={onSimulate} className="flex-1">
              {RETRY}
            </Button>
            {recordedHref ? (
              <Link to={recordedHref} className="inline-flex min-h-11 items-center rounded-sm border border-border-strong px-4 text-body font-medium text-text hover:bg-surface-2">
                {VIEW_RECORDED}
              </Link>
            ) : null}
          </>
        ) : (
          <Button variant="primary" onClick={onSimulate} disabled={disabled || status === 'running' || status === 'queued'} className="flex-1" data-testid="simulate">
            <Play size={16} aria-hidden="true" />
            {status === 'running' ? `${SCENARIO.running}…` : status === 'queued' ? 'Queued…' : SCENARIO.simulate}
            <span className="sr-only"> {scenario.skill}</span>
          </Button>
        )}
      </footer>
    </article>
  )
}
