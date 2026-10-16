import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { RatchetApi, StreamHandlers } from './client'
import type { Event } from './types'
import { BACKOFF_MAX_MS, LIMITED_RETRY_MS, useEventStream } from './useEventStream'

const ev = (id: number): Event => ({ id, ts: new Date().toISOString(), kind: 'verdict.decided', ref: 'v', payload: {}, session_hash: null })

function fakeApi(opts: { sse: boolean }) {
  const handlers: StreamHandlers[] = []
  const opened: (number | null)[] = []
  const api = {
    openStream: vi.fn((since: number | null, h: StreamHandlers) => {
      if (!opts.sse) return null
      opened.push(since)
      handlers.push(h)
      return { close: vi.fn() }
    }),
    listEvents: vi.fn(async ({ since }: { since?: number | null }) => ({ items: since == null ? [ev(5)] : [ev((since ?? 0) + 1)], next_cursor: null })),
  } as unknown as RatchetApi
  return { api, handlers, opened }
}

describe('useEventStream', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('delivers SSE events and reconnects from the last id after an error', async () => {
    const { api, handlers, opened } = fakeApi({ sse: true })
    const onEvent = vi.fn()
    const { result } = renderHook(() => useEventStream({ api, onEvent }))
    act(() => handlers[0]!.onOpen?.())
    expect(result.current.status).toBe('open')
    act(() => handlers[0]!.onEvent(ev(7)))
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ id: 7 }))
    act(() => handlers[0]!.onError?.())
    expect(result.current.status).toBe('connecting')
    await act(async () => vi.advanceTimersByTime(1500))
    expect(opened).toEqual([null, 7])
  })

  it('ignores duplicate ids', () => {
    const { api, handlers } = fakeApi({ sse: true })
    const onEvent = vi.fn()
    renderHook(() => useEventStream({ api, onEvent }))
    act(() => {
      handlers[0]!.onEvent(ev(3))
      handlers[0]!.onEvent(ev(3))
    })
    expect(onEvent).toHaveBeenCalledTimes(1)
  })

  it('falls back to polling when SSE is unavailable', async () => {
    const { api } = fakeApi({ sse: false })
    const onEvent = vi.fn()
    const { result } = renderHook(() => useEventStream({ api, onEvent, pollMs: 1000 }))
    expect(result.current.status).toBe('polling')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100)
    })
    expect(api.listEvents).toHaveBeenCalled()
    expect(onEvent).toHaveBeenCalledWith(expect.objectContaining({ id: 6 }))
  })

  it('pauses while the tab is hidden and resumes on visibility', () => {
    const { api, opened } = fakeApi({ sse: true })
    let state: DocumentVisibilityState = 'visible'
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => state)
    const { result } = renderHook(() => useEventStream({ api, onEvent: () => {} }))
    state = 'hidden'
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(result.current.status).toBe('paused')
    state = 'visible'
    act(() => document.dispatchEvent(new Event('visibilitychange')))
    expect(opened.length).toBe(2)
  })

  it('429 stream cap: polls at once, reports limited, and does not keep reconnecting', async () => {
    const { api, handlers, opened } = fakeApi({ sse: true })
    const { result } = renderHook(() => useEventStream({ api, onEvent: () => {}, pollMs: 1000 }))
    act(() => handlers[0]!.onError?.({ rateLimited: true }))
    expect(result.current.status).toBe('polling')
    expect(result.current.limited).toBe(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(BACKOFF_MAX_MS + 1000)
    })
    expect(opened).toHaveLength(1)
    expect(api.listEvents).toHaveBeenCalled()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIMITED_RETRY_MS)
    })
    expect(opened).toHaveLength(2)
    act(() => handlers[1]!.onOpen?.())
    expect(result.current.status).toBe('open')
    expect(result.current.limited).toBe(false)
  })
})
