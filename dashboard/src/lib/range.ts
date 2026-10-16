/**
 * Date windows for `GET /costs?from=&to=`. Windows are half-open `[from, to)` in
 * ISO 8601; day presets snap to local midnight so the query key is stable for the whole day.
 */
import type { CostsRange } from '../api/client'

export type RangePreset = 'today' | '7d' | '14d' | 'edit' | 'custom'
export const DEFAULT_PRESET: RangePreset = '14d'
export const PRESET_LABEL: Record<Exclude<RangePreset, 'custom'>, string> = {
  today: 'Today',
  '7d': '7 days',
  '14d': '14 days',
  edit: 'This edit',
}
const DAY = 86_400_000
/** The server refuses windows over 366 days. */
export const MAX_RANGE_DAYS = 366

export function startOfDay(d: Date): Date {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}

/** Local `YYYY-MM-DD` for `<input type="date">`. */
export function dateInput(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function parseDateInput(s: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null
}

/** Last `n` calendar days including today: `[midnight n-1 days ago, tomorrow's midnight)`. */
export function lastDays(n: number, now = new Date()): CostsRange {
  const today = startOfDay(now)
  const from = new Date(today)
  from.setDate(from.getDate() - (n - 1))
  const to = new Date(today)
  to.setDate(to.getDate() + 1)
  return { from: from.toISOString(), to: to.toISOString() }
}

export function presetRange(p: Exclude<RangePreset, 'edit' | 'custom'>, now = new Date()): CostsRange {
  return lastDays(p === 'today' ? 1 : p === '7d' ? 7 : 14, now)
}

/**
 * The edit's own window: detection to a minute after the verdict was written (the narrative
 * and the action's calls land just after the decision).
 */
export function editRange(detectedAt: string, decidedAt: string): CostsRange | null {
  const from = Date.parse(detectedAt)
  const to = Date.parse(decidedAt) + 60_000
  if (!Number.isFinite(from) || !Number.isFinite(to) || from >= to) return null
  return { from: new Date(from).toISOString(), to: new Date(to).toISOString() }
}

/** Inclusive calendar days from the two date inputs; null when invalid or over the cap. */
export function customRange(fromDay: string, toDay: string): CostsRange | null {
  const a = parseDateInput(fromDay)
  const b = parseDateInput(toDay)
  if (!a || !b || a > b) return null
  const end = new Date(b)
  end.setDate(end.getDate() + 1)
  if ((end.getTime() - a.getTime()) / DAY > MAX_RANGE_DAYS + 1) return null
  return { from: a.toISOString(), to: end.toISOString() }
}

/** The inputs' values for a window (the `to` bound is exclusive, so show the day before it). */
export function rangeInputs(r: CostsRange): { from: string; to: string } {
  const to = new Date(Date.parse(r.to) - 1)
  return { from: dateInput(new Date(r.from)), to: dateInput(to) }
}

/**
 * Fill the days a window covers so quiet days show as empty bars. Only the days the server
 * returned plus the gaps between the window's bounds; skipped for windows over two months.
 */
export function fillDays<T extends { day: string }>(days: T[], r: CostsRange | null, empty: (day: string) => T): T[] {
  if (!r) return days
  const from = startOfDay(new Date(r.from))
  const to = new Date(Date.parse(r.to) - 1)
  if ((to.getTime() - from.getTime()) / DAY > 62) return days
  const have = new Map(days.map((d) => [d.day, d]))
  const out: T[] = []
  for (let d = new Date(from); d <= to; d.setDate(d.getDate() + 1)) {
    const key = dateInput(d)
    out.push(have.get(key) ?? empty(key))
    have.delete(key)
  }
  // Server days outside the local calendar span (UTC edges) are kept, in order.
  return [...out, ...have.values()].sort((a, b) => a.day.localeCompare(b.day))
}
