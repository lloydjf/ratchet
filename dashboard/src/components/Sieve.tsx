import { useCallback, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import type { Event, PairOutcome, PairVerdict } from '../api/types'
import { TIERS } from '../api/types'
import { OUTCOME_LABEL, SIEVE } from '../copy'
import { OutcomeMark } from './Marks'
import { tiersWithSpend, usd } from '../lib/format'
import { dotPoint, hopPath, layoutSieve, pathOffsets, routePoints, sieveColumns, sieveGeometry, sieveTally, stageCounts, SIEVE_COLUMNS, type Point, type SieveDot, type SieveGeometry, type SieveStage } from '../lib/sieve'
import { usePrefersReducedMotion } from '../lib/usePrefersReducedMotion'
import { pairHref } from './PairGrid'

export const OUTCOME_COLOR: Record<PairOutcome, string> = {
  pass: 'var(--pass)',
  improved: 'var(--pass)',
  regression: 'var(--block)',
  drift: 'var(--drift)',
  uncertain: 'var(--warn)',
  failed: 'var(--text-muted)',
}
/** Column line inks: Level 0 is model-free (neutral); the three judges carry their tier hue. */
export const COLUMN_INK = ['var(--text-muted)', 'var(--nano)', 'var(--super)', 'var(--ultra)'] as const
const COLUMN_TEXT = ['var(--text-muted)', 'var(--nano-ink)', 'var(--super-ink)', 'var(--ultra-ink)'] as const

const HEIGHT = 220
const HOP_MS = 240
const EASE = 'cubic-bezier(0.16, 1, 0.3, 1)'

function decidedAt(d: SieveDot): string {
  if (!d.settled) return d.stage < 0 ? SIEVE.waiting : SIEVE.judging
  const by: string = d.decidedBy ?? (d.stage < 0 ? 'infra' : SIEVE_COLUMNS[d.stage as 0 | 1 | 2 | 3])
  return SIEVE.decidedAt[by] ?? by
}

function costLine(d: SieveDot): string {
  return `${usd(d.usd)} (${tiersWithSpend(d.usdByTier)})`
}

function dotLabel(d: SieveDot): string {
  const task = d.task || (d.golden ?? `Task ${d.index + 1}`)
  const outcome = d.settled && d.outcome ? `${OUTCOME_LABEL[d.outcome]}` : decidedAt(d)
  return d.settled ? `${task}. ${outcome}. Decided at ${decidedAt(d)}. ${costLine(d)}.` : `${task}. ${outcome}.`
}

/** Visual order: column by column, top to bottom. */
const byPosition = (a: SieveDot, b: SieveDot) => a.stage - b.stage || a.slot - b.slot

function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null)
  const [w, setW] = useState(fallback)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const measure = () => {
      const next = Math.round(el.getBoundingClientRect().width)
      if (next > 0) setW(next)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, w] as const
}

/** Hop the drawn dots from where they were to where the layout now puts them (transforms only). */
const ROUTE = 'color-mix(in srgb, var(--text-tertiary) 55%, var(--border))'
const pointsAttr = (pts: Point[]) => pts.map((p) => `${p.x},${p.y}`).join(' ')

function useHops(dots: SieveDot[], g: SieveGeometry, counts: Map<SieveStage, number>, enabled: boolean) {
  const els = useRef(new Map<string, SVGGElement>())
  const prev = useRef(new Map<string, { x: number; y: number; pathLen: number }>())
  const mounted = useRef(false)
  useLayoutEffect(() => {
    const next = new Map<string, { x: number; y: number; pathLen: number }>()
    for (const d of dots) {
      const p = dotPoint(g, d.stage, d.slot, counts.get(d.stage))
      next.set(d.key, { x: p.x, y: p.y, pathLen: d.path.length })
      const before = prev.current.get(d.key)
      const el = els.current.get(d.key)
      if (!enabled || !mounted.current || !el || typeof el.animate !== 'function') continue
      let pts: Point[] | null = null
      let hops = 1
      if (!before) pts = [{ x: p.x - 24, y: p.y }, p]
      else if (before.x !== p.x || before.y !== p.y) {
        // Pass through each column it entered on the way, on its own row, then turn into the berth.
        const via: Point[] = [{ x: before.x, y: before.y }]
        const entered = d.path.slice(before.pathLen)
        for (const s of entered.slice(0, -1)) if (s >= 0) via.push({ x: g.colX[s as 0 | 1 | 2 | 3], y: before.y })
        pts = [...via.slice(0, -1), ...hopPath(via.at(-1)!, p)]
        hops = Math.max(1, entered.length)
      }
      if (!pts) continue
      const offsets = pathOffsets(pts)
      for (const a of el.getAnimations()) a.cancel()
      el.animate(
        pts.map((q, i) => ({ transform: `translate(${q.x}px, ${q.y}px)`, offset: offsets[i] })),
        { duration: HOP_MS * hops, easing: EASE },
      )
    }
    prev.current = next
    mounted.current = true
  }, [dots, g, counts, enabled])
  return els
}

interface SieveProps {
  events: readonly Event[]
  pairs: readonly PairVerdict[]
  verdictId: string
  /** After `verdict.decided` the sieve no longer moves. */
  frozen?: boolean
  /** Total replays expected (Progress.n), for the entry count while running. */
  expected?: number | null
  title?: string
  className?: string
  onOpen?: (d: SieveDot) => void
}

/**
 * The cascade sieve: every replayed task is a dot that enters at the left as its replay
 * finishes and hops one column per cascade event, settling as a ring in its outcome colour
 * in the column that decided it. Hover or focus a dot for its task, outcome, deciding stage
 * and cost; click (or Enter) opens the pair.
 */
export function Sieve({ events, pairs, verdictId, frozen = false, expected, title = SIEVE.title, className = '', onOpen }: SieveProps) {
  const reduced = usePrefersReducedMotion()
  const navigate = useNavigate()
  const [wrapRef, width] = useWidth(960)
  const gridId = useId().replace(/:/g, '')
  const dots = useMemo(() => layoutSieve(events, pairs), [events, pairs])
  const narrow = width < 560
  const g = useMemo(() => sieveGeometry(width, HEIGHT, { entry: narrow ? 40 : 64, inset: narrow ? 12 : 16 }), [width, narrow])
  const counts = useMemo(() => stageCounts(dots), [dots])
  const els = useHops(dots, g, counts, !reduced && !frozen)
  const cols = useMemo(() => sieveColumns(dots), [dots])
  const tally = useMemo(() => sieveTally(dots), [dots])
  const decided = dots.filter((d) => d.settled).length
  const total = Math.max(dots.length, expected ?? 0)
  const ordered = useMemo(() => [...dots].sort(byPosition), [dots])
  const [active, setActive] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const shown = dots.find((d) => d.key === (hover ?? active)) ?? null

  const canOpen = (d: SieveDot) => d.settled && !!d.golden && !!d.replay
  const open = useCallback(
    (d: SieveDot) => {
      if (!d.golden || !d.replay || !d.settled) return
      onOpen?.(d)
      navigate(pairHref(verdictId, { golden_run_id: d.golden, replay_run_id: d.replay }))
    },
    [navigate, onOpen, verdictId],
  )

  const onKey = (e: KeyboardEvent<SVGGElement>, d: SieveDot) => {
    const i = ordered.indexOf(d)
    const go = (j: number) => {
      const t = ordered[Math.max(0, Math.min(ordered.length - 1, j))]
      if (!t) return
      setActive(t.key)
      els.current.get(t.key)?.focus()
    }
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') go(i + 1)
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') go(i - 1)
    else if (e.key === 'Home') go(0)
    else if (e.key === 'End') go(ordered.length - 1)
    else if (e.key === 'Enter' || e.key === ' ') open(d)
    else return
    e.preventDefault()
  }
  const tabKey = active && dots.some((d) => d.key === active) ? active : ordered[0]?.key

  const tip = shown ? dotPoint(g, shown.stage, shown.slot, counts.get(shown.stage)) : null
  const tipAlign = tip ? (tip.x < 140 ? 'start' : tip.x > width - 140 ? 'end' : 'center') : 'center'

  return (
    <section aria-labelledby={`${gridId}-t`} className={`flex flex-col ${className}`} data-testid="sieve" data-frozen={frozen || undefined} data-dots={dots.length} data-decided={decided}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-6 pt-5 max-md:px-4">
        <h2 id={`${gridId}-t`} className="text-body font-semibold text-text">
          {title}
        </h2>
        <p className="sr-only" aria-live="polite">
          {SIEVE.summary(total, decided)}
        </p>
      </div>
      <div className="flex flex-col md:flex-row">
        <div ref={wrapRef} className="relative min-w-0 flex-1" style={{ height: HEIGHT }}>
          <svg width={width} height={HEIGHT} className="block overflow-visible" role="group" aria-label={`${title}. ${SIEVE.keyboardHint}`}>
            <defs>
              <pattern id={`${gridId}-grid`} width="8" height="8" patternUnits="userSpaceOnUse">
                <path d="M8 0H0V8" fill="none" stroke="var(--grid-line)" strokeWidth="1" />
              </pattern>
            </defs>
            <rect x="0" y="0" width={width} height={HEIGHT} fill={`url(#${gridId}-grid)`} aria-hidden="true" />
            <g aria-hidden="true" fill="none" data-testid="sieve-routes">
              {dots
                .filter((d) => d.settled && d.stage >= 0)
                .map((d) => (
                  <polyline key={d.key} points={pointsAttr(routePoints(g, d, dots.length, counts))} style={{ stroke: d.key === (hover ?? active) ? 'var(--accent)' : ROUTE }} strokeWidth={1} strokeLinejoin="round" data-route={d.key} />
                ))}
            </g>
            <g aria-hidden="true" className="text-meta">
              {!narrow ? (
                <text x={g.entryX - 5} y={24} fill="var(--text-muted)" className="font-medium">
                  {SIEVE.entry}
                </text>
              ) : null}
              <line x1={g.entryX - 5} x2={g.entryX - 5} y1={g.fieldTop - 4} y2={g.fieldBottom + 4} stroke="var(--border-strong)" strokeDasharray="2 4" />
              {!narrow ? (
                <text x={g.entryX - 5} y={HEIGHT - 30} fill="var(--text-muted)" className="num font-mono">
                  {dots.length}
                  {expected ? `/${expected}` : ''}
                </text>
              ) : null}
              {cols.map((c, i) => {
                const x = g.colX[i as 0 | 1 | 2 | 3]
                return (
                  <g key={c.key}>
                    <text x={x - 5} y={24} fill={COLUMN_TEXT[i]} className="font-semibold">
                      {SIEVE.columns[c.key]}
                    </text>
                    <line x1={x} x2={x} y1={g.fieldTop - 4} y2={g.fieldBottom + 4} stroke={COLUMN_INK[i]} strokeWidth="2" strokeLinecap="square" />
                    <text x={x - 5} y={HEIGHT - 30} fill="var(--text)" className="num">
                      <tspan className="font-mono">{c.reached}</tspan>
                      {narrow ? null : <tspan fill="var(--text-muted)"> reached</tspan>}
                    </text>
                    {!narrow ? (
                      <text x={x - 5} y={HEIGHT - 14} fill="var(--text-muted)" className="num">
                        <tspan className="font-mono">{usd(i === 0 ? 0 : c.usd)}</tspan> {i === 0 ? SIEVE.noModel : TIERS[i - 1]}
                      </text>
                    ) : null}
                  </g>
                )
              })}
            </g>
            {dots.map((d) => {
              const p = dotPoint(g, d.stage, d.slot, counts.get(d.stage))
              const isActive = d.key === (hover ?? active)
              const color = d.outcome ? OUTCOME_COLOR[d.outcome] : 'var(--text-muted)'
              return (
                <g
                  key={d.key}
                  ref={(el) => {
                    if (el) els.current.set(d.key, el)
                    else els.current.delete(d.key)
                  }}
                  style={{ transform: `translate(${p.x}px, ${p.y}px)` }}
                  role="link"
                  aria-label={dotLabel(d)}
                  aria-disabled={!canOpen(d) || undefined}
                  tabIndex={d.key === tabKey ? 0 : -1}
                  className={`outline-none ${canOpen(d) ? 'cursor-pointer' : ''}`}
                  data-dot={d.key}
                  data-stage={d.stage}
                  data-outcome={d.outcome ?? undefined}
                  onMouseEnter={() => setHover(d.key)}
                  onMouseLeave={() => setHover((h) => (h === d.key ? null : h))}
                  onFocus={() => setActive(d.key)}
                  onBlur={() => setActive((a) => (a === d.key ? null : a))}
                  onClick={() => open(d)}
                  onKeyDown={(e) => onKey(e, d)}
                >
                  <circle r="8" fill="transparent" />
                  {isActive ? <circle r="8" fill="none" stroke="var(--accent)" strokeWidth="2" /> : null}
                  {d.settled ? (
                    <circle r="4" fill="var(--surface)" stroke={color} strokeWidth="2" strokeDasharray={d.outcome === 'failed' ? '2 2' : undefined} />
                  ) : (
                    <circle r="5" fill="var(--text-muted)" />
                  )}
                </g>
              )
            })}
          </svg>
          {shown && tip ? (
            <div
              role="tooltip"
              className="pointer-events-none absolute z-30 w-max max-w-[280px] border border-border-strong bg-surface-2 px-3 py-2 text-meta text-text"
              style={{
                left: tip.x,
                top: tip.y - 14,
                transform: `translate(${tipAlign === 'center' ? '-50%' : tipAlign === 'end' ? '-100%' : '-12px'}, -100%)`,
              }}
              data-testid="sieve-tooltip"
            >
              <p className="font-medium">{shown.task || shown.golden || `Task ${shown.index + 1}`}</p>
              {shown.settled && shown.outcome ? (
                <p className="mt-1 flex items-center gap-1.5" style={{ color: `var(--${outcomeInk(shown.outcome)})` }}>
                  <OutcomeMark outcome={shown.outcome} />
                  {OUTCOME_LABEL[shown.outcome]}
                </p>
              ) : null}
              <p className="mt-1 text-muted">
                {shown.settled ? `Decided at ${decidedAt(shown)}` : decidedAt(shown)}
              </p>
              <p className="num mt-0.5 text-muted">
                <span className="font-mono">{usd(shown.usd)}</span> ({tiersWithSpend(shown.usdByTier)})
              </p>
            </div>
          ) : null}
        </div>
        <DecidedRail tally={tally} decided={decided} total={total} />
      </div>
    </section>
  )
}

function outcomeInk(o: PairOutcome): string {
  return o === 'regression' ? 'block-ink' : o === 'uncertain' ? 'warn-ink' : o === 'drift' ? 'drift-ink' : o === 'failed' ? 'text-muted' : 'pass-ink'
}

function Ring({ outcome, size = 10 }: { outcome: PairOutcome; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="-5 -5 10 10" aria-hidden="true" className="shrink-0">
      <circle r="4" fill="none" stroke={OUTCOME_COLOR[outcome]} strokeWidth="2" strokeDasharray={outcome === 'failed' ? '2 2' : undefined} />
    </svg>
  )
}

function DecidedRail({ tally, decided, total }: { tally: [PairOutcome, number][]; decided: number; total: number }) {
  return (
    <aside className="flex shrink-0 flex-col gap-3 border-border px-6 py-4 max-md:border-t max-md:px-4 md:w-48 md:border-l md:py-0 md:pt-1" aria-label={SIEVE.rail} data-testid="sieve-rail">
      <p className="flex items-baseline justify-between gap-2 text-meta">
        <span className="font-semibold text-text">{SIEVE.rail}</span>
        <span className="num font-mono text-muted">
          {decided}/{total}
        </span>
      </p>
      <ul className="flex flex-wrap gap-x-5 gap-y-2 md:flex-col">
        {tally.map(([o, n]) => (
          <li key={o} className="flex items-center gap-2 text-meta" data-outcome={o}>
            <Ring outcome={o} />
            <span className="num w-5 font-mono text-text">{n}</span>
            <span className="text-muted">{OUTCOME_LABEL[o]}</span>
          </li>
        ))}
      </ul>
    </aside>
  )
}

/** The frozen sieve, small and static (timeline rows, the kit). Decorative: the row says the outcome. */
export function SieveThumb({ pairs, events = [], width = 112, height = 36, className = '' }: { pairs: readonly PairVerdict[]; events?: readonly Event[]; width?: number; height?: number; className?: string }) {
  const dots = useMemo(() => layoutSieve(events, pairs), [events, pairs])
  const g = useMemo(() => sieveGeometry(width, height, { pitch: 6, top: 3, bottom: 3, entry: 6, inset: 4 }), [width, height])
  const counts = useMemo(() => stageCounts(dots), [dots])
  return (
    <svg width={width} height={height} aria-hidden="true" className={`block shrink-0 ${className}`} data-testid="sieve-thumb">
      {dots
        .filter((d) => d.settled && d.stage >= 0)
        .map((d) => (
          <polyline key={`r${d.key}`} points={pointsAttr(routePoints(g, d, dots.length, counts, { fromRail: width < 160 }))} fill="none" style={{ stroke: ROUTE }} strokeWidth={0.75} strokeOpacity={width < 160 ? 0.5 : 1} />
        ))}
      {g.colX.map((x, i) => (
        <line key={i} x1={x} x2={x} y1={g.fieldTop} y2={g.fieldBottom} stroke={COLUMN_INK[i]} strokeWidth="1" />
      ))}
      {dots.map((d) => {
        const p = dotPoint(g, d.stage, d.slot, counts.get(d.stage))
        return <circle key={d.key} cx={p.x} cy={p.y} r="2" fill="var(--surface)" stroke={d.outcome ? OUTCOME_COLOR[d.outcome] : 'var(--text-muted)'} strokeWidth="1.25" />
      })}
    </svg>
  )
}
