/**
 * One app-wide event subscription. Routes events to query invalidations and to any
 * component that wants the raw feed (timeline Live mode, landing scenario cards).
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api } from './select'
import { qk } from './hooks'
import { useEventStream, type StreamStatus } from './useEventStream'
import type { Event, VerdictView } from './types'

type Listener = (e: Event) => void
interface LiveCtx {
  status: StreamStatus
  /** The host refused the stream (429 stream cap); the feed is polling instead. */
  limited: boolean
  subscribe: (fn: Listener) => () => void
}

const Ctx = createContext<LiveCtx>({ status: 'closed', limited: false, subscribe: () => () => {} })

/** Kinds that matter to the visible timeline (the kinds the timeline renders). */
export const TIMELINE_KINDS = new Set([
  'trace.blessed',
  'edit.detected',
  'verdict.decided',
  'verdict.acted',
  'verdict.rolled_back',
  'pair.reblessed',
  'pair.control',
  'digest.written',
])

export function routeEvent(qc: QueryClient, e: Event): void {
  const vid = typeof e.payload.verdict_id === 'string' ? e.payload.verdict_id : null
  if (vid) void qc.invalidateQueries({ queryKey: qk.verdict(vid) })
  if (e.kind.startsWith('verdict.') || e.kind.startsWith('simulate.')) {
    void qc.invalidateQueries({ queryKey: qk.verdict(e.ref) })
    void qc.invalidateQueries({ queryKey: qk.verdicts })
    void qc.invalidateQueries({ queryKey: qk.costs })
    void qc.invalidateQueries({ queryKey: qk.edits })
  }
  if (!vid && /^(edit|batch|pair|orchestrator)\./.test(e.kind)) {
    // No verdict id on the event: refresh any verdict still running.
    void qc.invalidateQueries({
      queryKey: ['verdict'],
      predicate: (q) => (q.state.data as VerdictView | undefined)?.verdict == null,
    })
  }
  if (e.kind === 'pair.reblessed') {
    void qc.invalidateQueries({ queryKey: ['pair'] })
    void qc.invalidateQueries({ queryKey: ['traces'] })
    void qc.invalidateQueries({ queryKey: ['trace'] })
  }
  if (e.kind === 'trace.blessed') void qc.invalidateQueries({ queryKey: ['traces'] })
  if (e.kind === 'digest.written') void qc.invalidateQueries({ queryKey: qk.digest })
  if (/^(batch|pair|verdict|simulate)\./.test(e.kind)) void qc.invalidateQueries({ queryKey: qk.ticker })
  if (TIMELINE_KINDS.has(e.kind)) void qc.invalidateQueries({ queryKey: qk.events })
}

export function LiveEventsProvider({ children, enabled = true }: { children: ReactNode; enabled?: boolean }) {
  const qc = useQueryClient()
  const listeners = useRef(new Set<Listener>())
  const onEvent = useCallback(
    (e: Event) => {
      routeEvent(qc, e)
      for (const l of listeners.current) l(e)
    },
    [qc],
  )
  const { status, limited } = useEventStream({ api: api(), enabled, onEvent })
  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn)
    return () => {
      listeners.current.delete(fn)
    }
  }, [])
  const value = useMemo(() => ({ status, limited, subscribe }), [status, limited, subscribe])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export function useLiveStatus(): StreamStatus {
  return useContext(Ctx).status
}

/** True while live updates are paused because the host's stream cap was hit. */
export function useLiveLimited(): boolean {
  return useContext(Ctx).limited
}

/** Subscribe to raw live events; the latest handler is always used. */
export function useLiveEvent(handler: Listener, enabled = true): void {
  const { subscribe } = useContext(Ctx)
  const ref = useRef(handler)
  useEffect(() => {
    ref.current = handler
  }, [handler])
  useEffect(() => {
    if (!enabled) return
    return subscribe((e) => ref.current(e))
  }, [subscribe, enabled])
}
