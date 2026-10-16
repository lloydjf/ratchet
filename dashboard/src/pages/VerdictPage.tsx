import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { responseToast, useMeta, useRebless, useVerdict } from '../api/hooks'
import type { PairVerdict } from '../api/types'
import { trackVerdictView } from '../analytics'
import { successToast } from '../copy'
import { ErrorState } from '../components/ErrorState'
import { useToast } from '../components/Toast'
import { pairKey } from '../lib/pairs'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useIsMobile } from '../lib/useMediaQuery'
import { DecidedHeader, RunningHeader } from './verdict/VerdictHeader'
import { VerdictEvidence } from './verdict/VerdictEvidence'
import { VerdictSide } from './verdict/VerdictSide'
import { VerdictSkeleton } from './verdict/VerdictSkeleton'

/** Verdict page: header band, then main column (2/3) and side column (1/3). */
export default function VerdictPage() {
  const { id = '' } = useParams()
  const q = useVerdict(id)
  const { data: meta } = useMeta()
  const mobile = useIsMobile()
  const rebless = useRebless()
  const toast = useToast()
  const [reblessed, setReblessed] = useState<Set<string>>(() => new Set())
  const view = q.data
  const v = view?.verdict ?? null
  const running = !!view && !v

  // Animate the chip only when the decision lands while this page is open.
  const [sawRunning, setSawRunning] = useState(false)
  if (running && !sawRunning) setSawRunning(true)

  useDocumentTitle(v ? [v.decision, v.affected_skills.join(', ')] : view ? ['Running', view.edit.affected_skills.join(', ')] : ['Verdict'])

  useEffect(() => {
    if (v) trackVerdictView(v.verdict_id)
  }, [v])

  if (q.isLoading) return <VerdictSkeleton />
  if (q.isError || !view) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />

  const doRebless = async (p: PairVerdict) => {
    const res = await rebless.mutateAsync({ golden: p.golden_run_id, replay: p.replay_run_id })
    setReblessed((s) => new Set(s).add(pairKey(p)))
    toast({ message: responseToast(res) ?? successToast.rebless(), tone: 'pass' })
  }

  return (
    <div className="flex flex-col gap-8">
      {v ? <DecidedHeader view={view} animate={sawRunning} simulatedMeta={!!meta?.simulated_models} publicMode={meta?.public_mode ?? true} /> : <RunningHeader view={view} />}
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-3">
        <VerdictEvidence view={view} verdictId={id} mobile={mobile} onRebless={doRebless} reblessed={reblessed} />
        <VerdictSide view={view} />
      </div>
    </div>
  )
}
