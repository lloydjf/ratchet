import { Link } from 'react-router-dom'
import { useVerdicts } from '../api/hooks'
import type { VerdictSummary } from '../api/types'
import { BADGE, EMPTY, OUTCOME_LABEL } from '../copy'
import { Badge } from '../components/Badge'
import { DecisionChip } from '../components/DecisionChip'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { SkeletonRows } from '../components/Skeleton'
import { dateTime, usd } from '../lib/format'
import { OUTCOME_ORDER } from '../lib/pairs'
import { useDocumentTitle } from '../lib/useDocumentTitle'

function Row({ v }: { v: VerdictSummary }) {
  const counts = OUTCOME_ORDER.filter((o) => v.outcome_counts[o]).map((o) => `${v.outcome_counts[o]} ${OUTCOME_LABEL[o].toLowerCase()}`)
  return (
    <li>
      <Link to={`/verdicts/${encodeURIComponent(v.verdict_id)}`} className="flex flex-col gap-2 px-4 py-4 hover:bg-surface sm:flex-row sm:items-start sm:gap-4" data-testid="verdict-row">
        <DecisionChip decision={v.decision} size="md" className="self-start" />
        <div className="min-w-0 flex-1">
          <p className="text-body font-medium text-text">{v.headline}</p>
          <p className="num mt-1 flex flex-wrap gap-x-3 gap-y-1 text-meta text-muted">
            <span className="font-mono text-text">{v.affected_skills.join(', ')}</span>
            <span>{counts.join(' · ')}</span>
            {v.recorded ? <Badge text={BADGE.recorded} /> : null}
            {v.simulated_models ? <Badge text={BADGE.simulated} tone="warn" /> : null}
          </p>
        </div>
        <div className="num flex shrink-0 gap-3 text-right font-mono text-mono text-muted sm:flex-col sm:gap-0.5">
          <time dateTime={v.created_at}>{dateTime(v.created_at)}</time>
          <span>{usd(v.usd_total)} (judges)</span>
        </div>
      </Link>
    </li>
  )
}

export default function VerdictsPage() {
  useDocumentTitle(['Verdicts'])
  const q = useVerdicts()
  return (
    <div className="flex flex-col gap-6">
      <header>
        <h1 className="text-h1 font-semibold text-text">Verdicts</h1>
        <p className="mt-1 text-body text-muted">Every skill edit Ratchet checked, newest first.</p>
      </header>
      {q.isLoading ? (
        <SkeletonRows rows={6} label="Loading verdicts" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => void q.refetch()} />
      ) : !q.data?.items.length ? (
        <EmptyState title={EMPTY.verdicts} action={{ label: 'Simulate a scenario', to: '/' }} />
      ) : (
        <ul className="divide-y divide-border overflow-hidden border border-border">
          {q.data.items.map((v) => (
            <Row key={v.verdict_id} v={v} />
          ))}
        </ul>
      )}
    </div>
  )
}
