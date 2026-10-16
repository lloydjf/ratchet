/**
 * Live events: SSE on `/api/v1/events/stream`, resuming from the last
 * event id, reconnecting with exponential backoff, pausing while the tab is hidden and
 * catching up on focus, and falling back to polling `/api/v1/events?since=` when SSE is
 * unavailable or keeps failing. A 429 `rate_limited` (the host's per-client or total stream
 * cap) switches to polling at once and reports `limited`, so the UI can say why instead of
 * showing a connection that never opens.
 */
import { useEffect, useRef, useState } from 'react'
import type { RatchetApi, StreamHandle } from './client'
import type { Event } from './types'

export type StreamStatus = 'connecting' | 'open' | 'polling' | 'paused' | 'closed'

export interface EventStreamOptions {
  api: RatchetApi
  enabled?: boolean
  onEvent: (event: Event) => void
  /** SSE failures in a row before switching to polling. */
  maxSseFailures?: number
  pollMs?: number
  /** Override for tests. */
  doc?: Document
}

export const BACKOFF_BASE_MS = 1000
export const BACKOFF_MAX_MS = 30_000
export const CATCH_UP_MS = 20_000
/** After a 429 the stream slots are full; wait longer before trying SSE again. */
export const LIMITED_RETRY_MS = 60_000

export function backoffDelay(attempt: number, jitter = Math.random()): number {
  const exp = Math.min(BACKOFF_MAX_MS, BACKOFF_BASE_MS * 2 ** Math.max(0, attempt - 1))
  return Math.round(exp * (0.8 + 0.4 * jitter))
}

export function useEventStream({
  api,
  enabled = true,
  onEvent,
  maxSseFailures = 3,
  pollMs = 3000,
  doc,
}: EventStreamOptions): { status: StreamStatus; lastEventId: number | null; limited: boolean } {
  const [status, setStatus] = useState<StreamStatus>(enabled ? 'connecting' : 'closed')
  const [limited, setLimited] = useState(false)
  const [lastEventId, setLastEventId] = useState<number | null>(null)
  const onEventRef = useRef(onEvent)
  useEffect(() => {
    onEventRef.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!enabled) return
    const d = doc ?? (typeof document === 'undefined' ? undefined : document)
    let lastId: number | null = null
    let handle: StreamHandle | null = null
    let failures = 0
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let pollTimer: ReturnType<typeof setInterval> | null = null
    let sseRetryTimer: ReturnType<typeof setTimeout> | null = null
    let catchUpTimer: ReturnType<typeof setInterval> | null = null
    let stopped = false
    const seen = new Set<number>()

    const deliver = (e: Event) => {
      if (seen.has(e.id)) return
      seen.add(e.id)
      if (seen.size > 2000) seen.clear()
      if (lastId == null || e.id > lastId) {
        lastId = e.id
        setLastEventId(e.id)
      }
      onEventRef.current(e)
    }

    const clearTimers = () => {
      if (reconnectTimer) clearTimeout(reconnectTimer)
      if (pollTimer) clearInterval(pollTimer)
      if (sseRetryTimer) clearTimeout(sseRetryTimer)
      if (catchUpTimer) clearInterval(catchUpTimer)
      reconnectTimer = pollTimer = sseRetryTimer = catchUpTimer = null
    }

    const teardown = () => {
      clearTimers()
      handle?.close()
      handle = null
    }

    const pollOnce = async () => {
      try {
        const page = await api.listEvents({ since: lastId, limit: 200 })
        const items = [...page.items].sort((a, b) => a.id - b.id)
        // With no cursor yet, only learn the position; old events are not "live".
        if (lastId == null) {
          const max = items.at(-1)?.id
          if (max != null) {
            lastId = max
            setLastEventId(max)
          }
          return
        }
        for (const e of items) deliver(e)
      } catch {
        /* keep polling; the next tick retries */
      }
    }

    const startPolling = (retryMs = BACKOFF_MAX_MS) => {
      if (stopped) return
      handle?.close()
      handle = null
      setStatus('polling')
      void pollOnce()
      if (!pollTimer) pollTimer = setInterval(() => void pollOnce(), pollMs)
      // Try SSE again later; a proxy hiccup should not pin us to polling forever.
      if (!sseRetryTimer)
        sseRetryTimer = setTimeout(() => {
          sseRetryTimer = null
          failures = 0
          if (pollTimer) clearInterval(pollTimer)
          pollTimer = null
          connect()
        }, retryMs)
    }

    const connect = () => {
      if (stopped) return
      setStatus('connecting')
      const h = api.openStream(lastId, {
        onEvent: deliver,
        onOpen: () => {
          failures = 0
          setLimited(false)
          setStatus('open')
          // Catch-up poll: picks up any kind the stream listener list does not name.
          if (!catchUpTimer) catchUpTimer = setInterval(() => void pollOnce(), CATCH_UP_MS)
        },
        onError: (info) => {
          if (stopped) return
          if (catchUpTimer) clearInterval(catchUpTimer)
          catchUpTimer = null
          h?.close()
          if (handle === h) handle = null
          if (info?.rateLimited) {
            // Retrying sooner only takes another slot from the same full pool.
            setLimited(true)
            startPolling(LIMITED_RETRY_MS)
            return
          }
          failures += 1
          if (failures >= maxSseFailures) {
            startPolling()
            return
          }
          setStatus('connecting')
          reconnectTimer = setTimeout(connect, backoffDelay(failures))
        },
      })
      if (!h) {
        startPolling()
        return
      }
      handle = h
    }

    const onVisibility = () => {
      if (!d) return
      if (d.visibilityState === 'hidden') {
        teardown()
        setStatus('paused')
      } else if (!handle && !pollTimer) {
        failures = 0
        connect()
      }
    }
    const onFocus = () => {
      if (d?.visibilityState !== 'hidden' && !handle && !pollTimer) connect()
    }

    if (d?.visibilityState === 'hidden') queueMicrotask(() => setStatus('paused'))
    else connect()
    d?.addEventListener('visibilitychange', onVisibility)
    if (typeof window !== 'undefined') window.addEventListener('focus', onFocus)

    return () => {
      stopped = true
      teardown()
      d?.removeEventListener('visibilitychange', onVisibility)
      if (typeof window !== 'undefined') window.removeEventListener('focus', onFocus)
    }
  }, [api, enabled, maxSseFailures, pollMs, doc])

  return { status: enabled ? status : 'closed', lastEventId, limited: enabled && limited }
}
