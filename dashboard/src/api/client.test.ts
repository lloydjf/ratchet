import { describe, expect, it, vi } from 'vitest'
import { createHttpApi, type StreamError } from './client'

class FakeES {
  static last: FakeES | null = null
  readyState = 0
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(readonly url: string) {
    FakeES.last = this
  }
  addEventListener() {}
  removeEventListener() {}
  close() {
    this.readyState = 2
  }
}

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

describe('http client', () => {
  it('sends the cost window as from/to', async () => {
    const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) => json(200, {}))
    const api = createHttpApi({ fetchImpl: fetchImpl as unknown as typeof fetch })
    await api.getCosts({ from: '2026-10-03T00:00:00.000Z', to: '2026-10-17T00:00:00.000Z' })
    expect(fetchImpl.mock.calls[0]![0]).toBe('/api/v1/costs?from=2026-10-03T00%3A00%3A00.000Z&to=2026-10-17T00%3A00%3A00.000Z')
    await api.getCosts()
    expect(fetchImpl.mock.calls[1]![0]).toBe('/api/v1/costs')
  })

  it('keeps the server message and status on 429s and 413s', async () => {
    const msg = 'You already have a scenario running. Wait for it to finish, then try another.'
    const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) => json(429, { error: { code: 'simulate_busy', message: msg, request_id: 'r1' } }))
    const api = createHttpApi({ fetchImpl: fetchImpl as unknown as typeof fetch })
    await expect(api.getEdit('e1')).rejects.toMatchObject({ code: 'simulate_busy', status: 429, message: msg })
  })

  it('reports a stream refused with 429 as rateLimited (EventSource hides the status)', async () => {
    const fetchImpl = vi.fn(async (_url: string, _init?: RequestInit) => json(429, { error: { code: 'rate_limited', message: 'x', request_id: 'r' } }))
    const api = createHttpApi({ fetchImpl: fetchImpl as unknown as typeof fetch, eventSource: FakeES as unknown as typeof EventSource })
    const errors: (StreamError | undefined)[] = []
    api.openStream(null, { onEvent: () => {}, onError: (i) => errors.push(i) })
    const es = FakeES.last!
    // A dropped connection the browser will retry: no probe.
    es.readyState = 0
    es.onerror?.()
    expect(errors).toEqual([undefined])
    expect(fetchImpl).not.toHaveBeenCalled()
    // The server answered with something other than a stream: probe once.
    es.readyState = 2
    es.onerror?.()
    await vi.waitFor(() => expect(errors).toHaveLength(2))
    expect(errors[1]).toEqual({ rateLimited: true })
    expect(fetchImpl.mock.calls[0]![0]).toBe('/api/v1/events/stream')
  })
})
