/**
 * Typed client for the Ratchet dashboard API (`/api/v1`, docs/CONTRACTS.md §7).
 *
 * `RatchetApi` is the seam: the HTTP implementation below talks to FastAPI; the fixture
 * implementation (`./fixtures`, dev only) serves the same shapes from memory.
 */
import type {
  ApiErrorBody,
  CostsView,
  DemoEvent,
  Digest,
  EditView,
  Event,
  LogsView,
  Meta,
  Page,
  PairView,
  ReblessResponse,
  RunsView,
  Scenario,
  SettingsView,
  SimulateResponseLoose,
  Ticker,
  Trace,
  TraceSummary,
  Verdict,
  VerdictSummary,
  VerdictView,
} from './types'
import { EVENT_KINDS } from './types'

export const API_PREFIX = '/api/v1'

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly requestId: string | null
  readonly details: unknown

  constructor(code: string, message: string, status: number, requestId: string | null = null, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.requestId = requestId
    this.details = details
  }
}

export function isApiError(e: unknown): e is ApiError {
  return e instanceof ApiError
}

export interface EventsQuery {
  since?: number | null
  kinds?: string[]
  limit?: number
  cursor?: string | null
}

export interface TracesQuery {
  kind?: 'golden' | 'live' | 'replay'
  skill?: string
  search?: string
}

export interface StreamError {
  /** The server refused the stream with 429 `rate_limited` (per-client or host stream cap). */
  rateLimited?: boolean
}

export interface StreamHandlers {
  onEvent: (event: Event) => void
  onOpen?: () => void
  onError?: (info?: StreamError) => void
}

export interface RunsQuery {
  /** Leave out the goldens (default true). */
  unblessed?: boolean
  limit?: number
}

/** `GET /costs?from=&to=`: an ISO 8601 window `[from, to)`. */
export interface CostsRange {
  from: string
  to: string
}

/** A live subscription. `close()` is idempotent. */
export interface StreamHandle {
  close: () => void
}

export interface RatchetApi {
  readonly mode: 'http' | 'fixtures'
  getMeta(): Promise<Meta>
  listScenarios(): Promise<Scenario[]>
  simulate(scenarioId: string): Promise<SimulateResponseLoose>
  listVerdicts(cursor?: string | null, limit?: number): Promise<Page<VerdictSummary>>
  getVerdict(id: string): Promise<VerdictView>
  getPair(verdictId: string, golden: string, replay: string): Promise<PairView>
  /** CONTRACTS §7 says VerdictView; actions.py returns Verdict. Both are handled. */
  rollback(verdictId: string): Promise<VerdictView | Verdict>
  approve(verdictId: string): Promise<VerdictView | Verdict>
  rebless(golden: string, replay: string): Promise<ReblessResponse>
  listEdits(): Promise<Page<EditView>>
  getEdit(id: string): Promise<EditView>
  replayEdit(id: string): Promise<EditView>
  /**
   * `GET /edits/{id}/logs` (operator host only; 403 `not_public` on the public host): each
   * batch's `worker.log` tail plus crash info, as `ratchet logs --edit` prints them. A 404
   * means an older backend that does not serve it.
   */
  getEditLogs(id: string): Promise<LogsView>
  /** `GET /runs?unblessed=1` (operator host only): recent captured and live runs, newest first. */
  listRuns(q?: RunsQuery): Promise<RunsView>
  listTraces(q?: TracesQuery): Promise<Page<TraceSummary>>
  getTrace(runId: string): Promise<Trace>
  bless(runId: string): Promise<Trace>
  unbless(runId: string): Promise<Trace>
  putCriteria(runId: string, criteria: string[]): Promise<Trace>
  getCosts(range?: CostsRange | null): Promise<CostsView>
  getDigest(): Promise<Digest>
  listEvents(q?: EventsQuery): Promise<Page<Event>>
  getTicker(): Promise<Ticker>
  getSettings(): Promise<SettingsView>
  postDemoEvent(event: DemoEvent): Promise<void>
  /** SSE on `/events/stream`. Returns null when the transport is unavailable. */
  openStream(since: number | null, handlers: StreamHandlers): StreamHandle | null
}

// ---------------------------------------------------------------- HTTP implementation

/** Listener names for EventSource: every known kind plus the default `message`. */
export const STREAM_EVENT_KINDS: readonly string[] = [...EVENT_KINDS, 'message']

async function parseError(res: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null
  try {
    body = (await res.json()) as Partial<ApiErrorBody>
  } catch {
    body = null
  }
  const err = body?.error
  const requestId = err?.request_id ?? res.headers.get('X-Request-Id')
  if (err?.code) return new ApiError(err.code, err.message ?? err.code, res.status, requestId, err.details)
  const code = res.status === 404 ? 'not_found' : res.status === 429 ? 'rate_limited' : `http_${res.status}`
  return new ApiError(code, res.statusText || code, res.status, requestId)
}

function qs(params: Record<string, string | number | null | undefined>): string {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') sp.set(k, String(v))
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export interface HttpApiOptions {
  base?: string
  fetchImpl?: typeof fetch
  eventSource?: typeof EventSource | undefined
}

export function createHttpApi(opts: HttpApiOptions = {}): RatchetApi {
  const base = opts.base ?? API_PREFIX
  const fetchImpl = opts.fetchImpl ?? ((...args: Parameters<typeof fetch>) => fetch(...args))
  const ES = opts.eventSource ?? (typeof EventSource === 'undefined' ? undefined : EventSource)
  let metaPromise: Promise<Meta> | null = null

  async function request<T>(method: string, path: string, body?: unknown, action = false, retried = false): Promise<T> {
    const headers: Record<string, string> = { Accept: 'application/json' }
    if (body !== undefined) headers['Content-Type'] = 'application/json'
    if (action) {
      const meta = await getMeta().catch(() => null)
      if (meta?.action_token) headers['X-Ratchet-Action-Token'] = meta.action_token
    }
    let res: Response
    try {
      res = await fetchImpl(`${base}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'same-origin',
      })
    } catch (e) {
      throw new ApiError('network', e instanceof Error ? e.message : 'Network error', 0)
    }
    if (!res.ok) {
      const err = await parseError(res)
      // The demo action token rotates daily; fetch a fresh one and retry once.
      if (action && err.code === 'action_token_invalid' && !retried) {
        metaPromise = null
        return request<T>(method, path, body, action, true)
      }
      throw err
    }
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }

  function getMeta(): Promise<Meta> {
    if (!metaPromise) {
      metaPromise = request<Meta>('GET', '/meta').catch((e: unknown) => {
        metaPromise = null
        throw e
      })
    }
    return metaPromise
  }

  /** True when the stream endpoint answers 429 (stream cap). Aborts as soon as headers arrive. */
  async function probeStream(url: string): Promise<boolean> {
    const ctl = typeof AbortController === 'undefined' ? null : new AbortController()
    try {
      const res = await fetchImpl(url, { headers: { Accept: 'text/event-stream' }, credentials: 'same-origin', signal: ctl?.signal })
      const limited = res.status === 429
      ctl?.abort()
      return limited
    } catch {
      return false
    }
  }

  const enc = encodeURIComponent

  return {
    mode: 'http',
    getMeta,
    listScenarios: () => request('GET', '/scenarios'),
    simulate: (id) => request('POST', `/simulate/${enc(id)}`, {}, true),
    listVerdicts: (cursor, limit = 50) => request('GET', `/verdicts${qs({ cursor, limit })}`),
    getVerdict: (id) => request('GET', `/verdicts/${enc(id)}`),
    getPair: (id, g, r) => request('GET', `/verdicts/${enc(id)}/pairs/${enc(g)}/${enc(r)}`),
    rollback: (id) => request('POST', `/verdicts/${enc(id)}/rollback`, {}, true),
    approve: (id) => request('POST', `/verdicts/${enc(id)}/approve`, {}, true),
    rebless: (g, r) => request('POST', `/pairs/${enc(g)}/${enc(r)}/rebless`, {}, true),
    listEdits: () => request('GET', '/edits'),
    getEdit: (id) => request('GET', `/edits/${enc(id)}`),
    replayEdit: (id) => request('POST', `/edits/${enc(id)}/replay`, {}, true),
    getEditLogs: (id) => request('GET', `/edits/${enc(id)}/logs`),
    listRuns: (q = {}) => request('GET', `/runs${qs({ unblessed: q.unblessed === false ? 0 : 1, limit: q.limit ?? 50 })}`),
    listTraces: (q = {}) => request('GET', `/traces${qs({ kind: q.kind ?? 'golden', skill: q.skill, search: q.search })}`),
    getTrace: (runId) => request('GET', `/traces/${enc(runId)}`),
    bless: (runId) => request('POST', `/runs/${enc(runId)}/bless`, {}, true),
    unbless: (runId) => request('POST', `/traces/${enc(runId)}/unbless`, {}, true),
    putCriteria: (runId, criteria) => request('PUT', `/traces/${enc(runId)}/criteria`, { criteria }, true),
    getCosts: (range) => request('GET', `/costs${qs({ from: range?.from, to: range?.to })}`),
    getDigest: () => request('GET', '/digest/latest'),
    listEvents: (q = {}) =>
      request('GET', `/events${qs({ since: q.since, kinds: q.kinds?.join(','), limit: q.limit, cursor: q.cursor })}`),
    getTicker: () => request('GET', '/ticker'),
    getSettings: () => request('GET', '/settings'),
    async postDemoEvent(event) {
      const url = `${base}/demo/events`
      const payload = JSON.stringify(event)
      try {
        const res = await fetchImpl(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: payload,
          keepalive: true,
          credentials: 'same-origin',
        })
        if (!res.ok) throw new Error(String(res.status))
      } catch {
        // Fire-and-forget; sendBeacon survives navigation and blocked fetches.
        try {
          if (typeof navigator !== 'undefined' && navigator.sendBeacon) {
            navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }))
          }
        } catch {
          /* never block the UI on analytics */
        }
      }
    },
    openStream(since, handlers) {
      if (!ES) return null
      // EventSource cannot set headers on the first request; `since` carries the cursor.
      // On its own automatic reconnects the browser sends `Last-Event-ID`.
      const url = `${base}/events/stream${qs({ since })}`
      const src = new ES(url, { withCredentials: true })
      let closed = false
      const listener = (e: MessageEvent<string>) => {
        try {
          handlers.onEvent(JSON.parse(e.data) as Event)
        } catch {
          /* ignore malformed frames */
        }
      }
      for (const kind of STREAM_EVENT_KINDS) src.addEventListener(kind, listener as EventListener)
      src.onopen = () => handlers.onOpen?.()
      src.onerror = () => {
        // CONNECTING: a dropped connection the browser retries. CLOSED: the server answered
        // with something other than a stream (EventSource hides the status), so ask once.
        if (src.readyState !== 2) {
          handlers.onError?.()
          return
        }
        void probeStream(url).then((rateLimited) => {
          if (!closed) handlers.onError?.({ rateLimited })
        })
      }
      return {
        close() {
          if (closed) return
          closed = true
          for (const kind of STREAM_EVENT_KINDS) src.removeEventListener(kind, listener as EventListener)
          src.close()
        },
      }
    },
  }
}
