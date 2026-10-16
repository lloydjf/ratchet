/**
 * Anonymous demo funnel. Public host only. No IPs, no user agents beyond a
 * device class, no cross-session ids, no third-party scripts. The session hash lives in
 * sessionStorage only. Fire-and-forget: never blocks or breaks the UI.
 */
import type { DemoEvent, DemoEventName } from './api/types'
import type { RatchetApi } from './api/client'

const KEY = 'ratchet.demo.session'
const CLICK_KEY = 'ratchet.demo.clicks'

let enabled = false
let target: RatchetApi | null = null
let memorySession: string | null = null

export function configureAnalytics(next: { api: RatchetApi; publicMode: boolean }): void {
  target = next.api
  enabled = next.publicMode
}

function randomId(): string {
  try {
    const b = new Uint8Array(12)
    crypto.getRandomValues(b)
    return Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('')
  } catch {
    return Math.random().toString(16).slice(2) + Date.now().toString(16)
  }
}

export function sessionHash(): string {
  try {
    const existing = sessionStorage.getItem(KEY)
    if (existing) return existing
    const id = randomId()
    sessionStorage.setItem(KEY, id)
    return id
  } catch {
    memorySession ??= randomId()
    return memorySession
  }
}

export function deviceClass(width = typeof window === 'undefined' ? 1280 : window.innerWidth): DemoEvent['device'] {
  if (width < 768) return 'mobile'
  if (width < 1024) return 'tablet'
  return 'desktop'
}

export function referrerBucket(ref = typeof document === 'undefined' ? '' : document.referrer): DemoEvent['referrer'] {
  if (!ref) return 'none'
  let host: string
  try {
    host = new URL(ref).hostname.toLowerCase()
  } catch {
    return 'other'
  }
  if (typeof window !== 'undefined' && host === window.location.hostname) return 'none'
  if (host.endsWith('devpost.com')) return 'devpost'
  if (host.endsWith('youtube.com') || host === 'youtu.be') return 'youtube'
  if (host.endsWith('github.com') || host.endsWith('github.io')) return 'github'
  return 'other'
}

export function track(name: DemoEventName, props: Record<string, string | number> = {}): void {
  if (!enabled || !target) return
  const clean: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(props)) clean[k] = typeof v === 'number' ? Math.round(v) : String(v)
  const event: DemoEvent = {
    kind: name,
    session_hash: sessionHash(),
    ts: new Date().toISOString(),
    device: deviceClass(),
    referrer: referrerBucket(),
    props: clean,
  }
  const t = target
  const send = () => void t.postDemoEvent(event).catch(() => {})
  if (typeof requestIdleCallback === 'function') requestIdleCallback(send, { timeout: 2000 })
  else setTimeout(send, 0)
}

/** Remember when Simulate was clicked so `demo.verdict_view` can report seconds since. */
export function markSimulateClick(scenario: string, verdictId: string): void {
  try {
    const all = JSON.parse(sessionStorage.getItem(CLICK_KEY) ?? '{}') as Record<string, { scenario: string; at: number }>
    all[verdictId] = { scenario, at: Date.now() }
    sessionStorage.setItem(CLICK_KEY, JSON.stringify(all))
  } catch {
    /* storage unavailable */
  }
}

export function trackVerdictView(verdictId: string): void {
  try {
    const all = JSON.parse(sessionStorage.getItem(CLICK_KEY) ?? '{}') as Record<string, { scenario: string; at: number; seen?: boolean }>
    const hit = all[verdictId]
    if (!hit || hit.seen) return
    hit.seen = true
    sessionStorage.setItem(CLICK_KEY, JSON.stringify(all))
    track('demo.verdict_view', { scenario: hit.scenario, verdict_id: verdictId, seconds: Math.round((Date.now() - hit.at) / 1000) })
  } catch {
    /* storage unavailable */
  }
}
