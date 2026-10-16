import type { Tier, TierMap } from '../api/types'
import { TIERS } from '../api/types'

/** Money: "$0.0042" under a cent, "$0.12" otherwise. Never a string from the API. */
export function usd(value: number | null | undefined): string {
  const v = value ?? 0
  if (v === 0) return '$0.00'
  const abs = Math.abs(v)
  if (abs < 0.01) return `$${v.toFixed(4)}`
  if (abs < 1) return `$${v.toFixed(3)}`
  return `$${v.toFixed(2)}`
}

/** "$0.04 (nano)" — costs always with tier. */
export function usdTier(value: number, tier: Tier): string {
  return `${usd(value)} (${tier})`
}

export function sumTiers(map: TierMap<number> | null | undefined): number {
  if (!map) return 0
  return TIERS.reduce((acc, t) => acc + (map[t] ?? 0), 0)
}

export function tokens(n: number | null | undefined): string {
  const v = n ?? 0
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`
  if (v >= 10_000) return `${Math.round(v / 1000)}k`
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`
  return `${v}`
}

export function ratio(actual: number, counterfactual: number): number {
  if (actual <= 0) return 0
  return counterfactual / actual
}

export function ratioLabel(r: number): string {
  if (!Number.isFinite(r) || r <= 0) return '0'
  return r >= 10 ? r.toFixed(0) : r.toFixed(1)
}

export function pct(conf: number): string {
  return `${Math.round(conf * 100)}%`
}

export function duration(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(seconds ?? 0))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  const r = s % 60
  return r ? `${m} min ${r} s` : `${m} min`
}

export function elapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

const timeFmt = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })
const dateTimeFmt = new Intl.DateTimeFormat('en-GB', {
  day: 'numeric',
  month: 'short',
  hour: '2-digit',
  minute: '2-digit',
  timeZoneName: 'short',
})
const dayFmt = new Intl.DateTimeFormat('en-GB', { weekday: 'long', day: 'numeric', month: 'long' })
const shortDayFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short' })

/** "14:02 GMT+2" — a date always shows its timezone. */
export function time(iso: string | null | undefined): string {
  if (!iso) return '—'
  return timeFmt.format(new Date(iso))
}
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return dateTimeFmt.format(new Date(iso))
}
/** Parses "YYYY-MM-DD" as a local calendar date (not UTC midnight). */
function parseDay(iso: string): Date {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(iso)
}

export function shortDay(iso: string): string {
  return shortDayFmt.format(parseDay(iso))
}

export function dayKey(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export function dayHeading(iso: string, now = new Date()): string {
  const key = dayKey(iso)
  if (key === dayKey(now.toISOString())) return 'Today'
  const y = new Date(now)
  y.setDate(y.getDate() - 1)
  if (key === dayKey(y.toISOString())) return 'Yesterday'
  return dayFmt.format(new Date(iso))
}

export function relative(iso: string, now = Date.now()): string {
  const diff = Math.round((now - new Date(iso).getTime()) / 1000)
  if (diff < 45) return 'just now'
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`
  return `${Math.round(diff / 86400)} d ago`
}

export function truncate(text: string, max = 80): string {
  const t = text.replace(/\s+/g, ' ').trim()
  return t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—'
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Tiers that incurred spend, for "$0.04 (nano, super)" (never a bare dollar figure). */
export function tiersWithSpend(map: TierMap<number> | null | undefined, none = 'no model'): string {
  if (!map) return none
  return TIERS.filter((t) => (map[t] ?? 0) > 0).join(', ') || none
}

