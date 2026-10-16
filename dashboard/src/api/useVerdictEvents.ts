import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { eventsForVerdict } from '../lib/sieve'
import { useLiveEvent } from './live'
import { api } from './select'
import type { Event } from './types'

/** The event kinds the sieve folds: replay batches and the per-pair cascade. */
export const SIEVE_KINDS = ['batch.submitted', 'batch.heartbeat', 'batch.finished', 'pair.level0', 'pair.opinion', 'pair.drift', 'pair.control', 'pair.decided', 'verdict.decided'] as const
const KIND_SET = new Set<string>(SIEVE_KINDS)

/**
 * One verdict's cascade events: what the journal already holds (so a page opened mid-run
 * starts where the run is) plus every live event since. Only while `enabled` (the run was
 * seen running on this page); a verdict opened after it was decided is drawn from its pairs.
 */
export function useVerdictEvents(verdictId: string, editId: string | null | undefined, enabled: boolean): Event[] {
  const [live, setLive] = useState<Event[]>([])
  const history = useQuery({
    queryKey: ['verdict-events', verdictId],
    queryFn: () => api().listEvents({ kinds: [...SIEVE_KINDS], limit: 200 }),
    enabled: enabled && !!verdictId,
    staleTime: Infinity,
    retry: false,
  })
  useLiveEvent((e) => {
    if (!KIND_SET.has(e.kind)) return
    setLive((xs) => (xs.some((x) => x.id === e.id) ? xs : [...xs, e]))
  }, enabled)
  return useMemo(() => {
    if (!enabled) return []
    const seen = new Set<number>()
    const all: Event[] = []
    for (const e of [...(history.data?.items ?? []), ...live]) {
      if (seen.has(e.id) || !KIND_SET.has(e.kind)) continue
      seen.add(e.id)
      all.push(e)
    }
    return eventsForVerdict(all, verdictId, editId)
  }, [enabled, history.data, live, verdictId, editId])
}
