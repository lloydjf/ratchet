import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { responseToast, useMeta, useRebless, useVerdict } from '../api/hooks'
import { useVerdictEvents } from '../api/useVerdictEvents'
import type { PairVerdict } from '../api/types'
import { trackVerdictView } from '../analytics'
import { successToast } from '../copy'
import { ErrorState } from '../components/ErrorState'
import { parseUnifiedDiff } from '../components/DiffViewer'
import { canOpen, pairHref, pairRowId } from '../components/PairGrid'
import { Sieve } from '../components/Sieve'
import { useToast } from '../components/Toast'
import { useHotkeys } from '../lib/commands'
import { hunkPairs, hunksOf } from '../lib/hunks'
import { pairKey, sortPairs } from '../lib/pairs'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useIsMobile } from '../lib/useMediaQuery'
import { VerdictBand } from './verdict/VerdictHeader'
import { DiffPanel, DriftPanel, NarrativePanel, PairsPanel } from './verdict/VerdictEvidence'
import { DebugDrawer, DetailsPanel } from './verdict/VerdictSide'
import { VerdictSkeleton } from './verdict/VerdictSkeleton'

const clickAction = (...kinds: string[]) => {
  for (const k of kinds) {
    const el = document.querySelector<HTMLButtonElement>(`[data-testid="verdict-actions"] button[data-action="${k}"]:not([disabled])`)
    if (el) {
      el.click()
      return
    }
  }
}

/**
 * Verdict page: one column of hairline-separated panels. The decision band, the cascade
 * sieve, the pair grid, the diff (each hunk filters the grid), the summary, drift, cost and
 * run details. Keys: j/k select a pair, o opens it, r rolls back, a approves.
 */
export default function VerdictPage() {
  const { id = '' } = useParams()
  const q = useVerdict(id)
  const { data: meta } = useMeta()
  const mobile = useIsMobile()
  const navigate = useNavigate()
  const rebless = useRebless()
  const toast = useToast()
  const [reblessed, setReblessed] = useState<Set<string>>(() => new Set())
  const [hunk, setHunk] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const view = q.data
  const v = view?.verdict ?? null
  const running = !!view && !v

  // The sieve and the band animate only when the decision lands while this page is open.
  const [sawRunning, setSawRunning] = useState(false)
  if (running && !sawRunning) setSawRunning(true)
  const events = useVerdictEvents(id, view?.edit.edit_id, running || sawRunning)

  useDocumentTitle(v ? [v.decision, v.affected_skills.join(', ')] : view ? ['Running', view.edit.affected_skills.join(', ')] : ['Verdict'])

  useEffect(() => {
    if (v) trackVerdictView(v.verdict_id)
  }, [v])

  const hunkMap = useMemo(() => (v && view ? hunkPairs(hunksOf(parseUnifiedDiff(view.diff)), v.pairs) : null), [v, view])
  const hunkCounts = useMemo(() => (hunkMap ? new Map([...hunkMap].map(([k, keys]) => [k, keys.length])) : undefined), [hunkMap])
  const only = useMemo(() => (hunk && hunkMap?.get(hunk) ? new Set(hunkMap.get(hunk)) : null), [hunk, hunkMap])
  const order = useMemo(() => (v ? sortPairs(only ? v.pairs.filter((p) => only.has(pairKey(p))) : v.pairs) : []), [v, only])

  const move = (d: number) => {
    if (!order.length) return
    const i = order.findIndex((p) => pairKey(p) === selected)
    const next = order[i < 0 ? (d > 0 ? 0 : order.length - 1) : Math.max(0, Math.min(order.length - 1, i + d))]!
    setSelected(pairKey(next))
    document.getElementById(pairRowId(next))?.scrollIntoView({ block: 'nearest' })
  }
  useHotkeys(
    {
      j: () => move(1),
      k: () => move(-1),
      o: () => {
        const p = order.find((x) => pairKey(x) === selected)
        if (p && canOpen(p)) navigate(pairHref(id, p))
      },
      r: () => clickAction('rollback'),
      a: () => clickAction('approve', 'approveAnyway'),
    },
    !!v,
  )

  if (q.isLoading) return <VerdictSkeleton />
  if (q.isError || !view) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />

  const doRebless = async (p: PairVerdict) => {
    const res = await rebless.mutateAsync({ golden: p.golden_run_id, replay: p.replay_run_id })
    setReblessed((s) => new Set(s).add(pairKey(p)))
    toast({ message: responseToast(res) ?? successToast.rebless(), tone: 'pass' })
  }

  const pickHunk = (h: string | null) => {
    setHunk(h)
    if (h) document.getElementById('pairs-title')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }

  return (
    <div className="stack" data-testid="verdict-page">
      <VerdictBand view={view} animate={sawRunning} simulatedMeta={!!meta?.simulated_models} publicMode={meta?.public_mode ?? true} />
      <Sieve events={events} pairs={v?.pairs ?? []} verdictId={id} frozen={!!v} expected={view.progress?.n ?? null} />
      <PairsPanel view={view} verdictId={id} only={only} onClearFilter={() => setHunk(null)} selected={selected} />
      <DiffPanel view={view} mobile={mobile} hunkCounts={hunkCounts} activeHunk={hunk} onHunk={pickHunk} />
      {v ? <NarrativePanel v={v} /> : null}
      {v ? <DriftPanel v={v} onRebless={doRebless} reblessed={reblessed} /> : null}
      <DetailsPanel view={view} />
      {v ? <DebugDrawer v={v} editId={view.edit.edit_id} /> : null}
    </div>
  )
}
