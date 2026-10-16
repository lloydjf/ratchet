import { useEffect, useState } from 'react'
import type { ProgressStage, Tier, TierMap } from '../api/types'
import { TIERS } from '../api/types'
import { STAGE_KEYS, STALL_AFTER_S, stageLabel, stageLabelShort, stalledCopy } from '../copy'
import { elapsed as fmtElapsed, tiersWithSpend, usd } from '../lib/format'

export type StripStatus = 'running' | 'stalled' | 'done' | 'failed'

interface ProgressStripProps {
  stage: string
  stageLabel?: string
  k?: number | null
  n?: number | null
  tier?: Tier | null
  elapsedS: number
  usdByTier: TierMap<number>
  stages?: ProgressStage[]
  /** Server-side stall flag; the strip also detects > 20 s without a change itself. */
  stalled?: boolean
  status?: 'running' | 'done' | 'failed'
  failedMessage?: string
  compact?: boolean
  /** Advance the elapsed clock locally between server updates. */
  tickLocally?: boolean
  className?: string
}

const TIER_STAGES: Record<string, Tier> = { nano: 'nano', super: 'super', ultra: 'ultra' }

export function tierForStage(stage: string): Tier | null {
  return TIER_STAGES[stage] ?? (stage === 'narrative' ? 'ultra' : null)
}

const KN = /\((\d+)\/(\d+)\)/

/**
 * ADR 0023: the controls stage counts control batches (`ctrl_…`). `Progress.k/n` always
 * count the edit's own replays, so the controls k/n come from the server's controls label.
 */
export function controlCounts(label?: string | null, stages?: ProgressStage[]): [number | null, number | null] {
  for (const src of [label, stages?.find((s) => s.key === 'controls')?.label]) {
    const m = src ? KN.exec(src) : null
    if (m) return [Number(m[1]), Number(m[2])]
  }
  return [null, null]
}

/** Detects a stage label that has not changed for STALL_AFTER_S seconds. */
export function useStalled(label: string, active: boolean, thresholdS = STALL_AFTER_S): boolean {
  const [state, setState] = useState({ label, stalled: false })
  if (state.label !== label) setState({ label, stalled: false })
  useEffect(() => {
    if (!active) return
    const t = setTimeout(() => setState({ label, stalled: true }), thresholdS * 1000)
    return () => clearTimeout(t)
  }, [label, active, thresholdS])
  return state.label === label && state.stalled
}

function useLocalClock(baseS: number, enabled: boolean): number {
  // Seconds elapsed locally since the server last reported `baseS`.
  const [extra, setExtra] = useState({ baseS, s: 0 })
  if (extra.baseS !== baseS) setExtra({ baseS, s: 0 })
  useEffect(() => {
    if (!enabled) return
    const id = setInterval(() => setExtra((e) => (e.baseS === baseS ? { baseS, s: e.s + 1 } : e)), 1000)
    return () => clearInterval(id)
  }, [enabled, baseS])
  return enabled ? baseS + extra.s : baseS
}

/**
 * The run's stage, tier, elapsed time and running cost. The stage label set is fixed
 * (`STAGE_KEYS` in copy.ts). Announces stage changes politely; pulses subtly when stalled.
 */
export function ProgressStrip({
  stage,
  stageLabel: label,
  k,
  n,
  tier,
  elapsedS,
  usdByTier,
  stages,
  stalled: serverStalled = false,
  status = 'running',
  failedMessage,
  compact = false,
  tickLocally = true,
  className = '',
}: ProgressStripProps) {
  const controls = stage === 'controls' || stage === 'controlling'
  const [sk, sn] = controls ? controlCounts(label, stages) : [k, n]
  const text = label ?? stageLabel(stage, sk, sn)
  const running = status === 'running'
  const localStall = useStalled(text, running)
  const stalled = running && (serverStalled || localStall)
  const shownElapsed = useLocalClock(elapsedS, running && tickLocally)
  const effTier = tier ?? tierForStage(stage)
  const total = TIERS.reduce((a, t) => a + (usdByTier[t] ?? 0), 0)
  const visual: StripStatus = status === 'failed' ? 'failed' : status === 'done' ? 'done' : stalled ? 'stalled' : 'running'

  const segs: ProgressStage[] =
    stages && stages.length
      ? stages.filter((s) => s.key !== 'done')
      : STAGE_KEYS.filter((s) => s !== 'done').map((key) => {
          const idx = STAGE_KEYS.indexOf(key)
          const cur = STAGE_KEYS.indexOf(stage as (typeof STAGE_KEYS)[number])
          const st: ProgressStage['status'] = status === 'done' || idx < cur ? 'done' : idx === cur ? 'active' : 'pending'
          return { key, label: stageLabel(key), status: st, detail: null }
        })

  return (
    <div
      className={`rounded-card border border-border bg-surface ${compact ? 'p-3' : 'p-4'} ${className}`}
      data-testid="progress-strip"
      data-status={visual}
      data-stage={stage}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="min-w-0 text-body font-semibold text-text" aria-live="polite" aria-atomic="true">
          <span className="sr-only">Progress: </span>
          <span key={text} className={`anim-stage ${compact ? 'hidden sm:inline' : ''}`}>
            {status === 'failed' ? (failedMessage ?? 'The run failed.') : status === 'done' ? 'Done' : text}
          </span>
          {compact ? (
            <span className="sm:hidden" aria-hidden="true">
              {status === 'done' ? 'Done' : stageLabelShort(stage, sk, sn)}
            </span>
          ) : null}
          {stalled ? <span className="sr-only">. {stalledCopy(text)}</span> : null}
        </p>
        <p className="num flex shrink-0 items-center gap-3 font-mono text-mono text-muted">
          {effTier && running ? (
            <span className="font-semibold" style={{ color: `var(--${effTier}-ink)` }}>
              {effTier}
            </span>
          ) : null}
          <span aria-label={`Elapsed ${Math.floor(shownElapsed)} seconds`}>{fmtElapsed(shownElapsed)}</span>
          <span aria-label={`Running cost ${usd(total)} (${tiersWithSpend(usdByTier, 'no model yet')})`} data-testid="strip-cost">
            {usd(total)} ({tiersWithSpend(usdByTier, 'no model yet')})
          </span>
        </p>
      </div>
      <ol className="mt-3 flex gap-1" aria-label="Stages">
        {segs.map((s) => {
          const t = tierForStage(s.key)
          const color =
            visual === 'failed' && s.status === 'active'
              ? 'var(--block)'
              : s.status === 'skipped'
                ? 'transparent'
                : s.status === 'pending'
                  ? 'var(--surface-2)'
                  : t
                    ? `var(--${t})`
                    : s.key === 'drift'
                      ? 'var(--drift)'
                      : 'var(--accent)'
          return (
            <li key={s.key} className="min-w-0 flex-1" aria-label={`${s.label}: ${s.status}`} data-stage-status={s.status}>
              <span
                className={`block h-1.5 rounded-full transition-[background-color,opacity] duration-[160ms] ease-out ${s.status === 'skipped' ? 'border border-dashed border-border-strong' : ''} ${s.status === 'active' && visual === 'stalled' ? 'anim-stalled' : ''}`}
                style={{ background: color, opacity: s.status === 'active' && visual === 'running' ? 0.75 : 1 }}
              />
            </li>
          )
        })}
      </ol>
      {stalled ? (
        <p className="mt-2 text-meta text-warn-ink" data-testid="stalled-copy">
          {stalledCopy(text)}
        </p>
      ) : null}
      {!compact && stages && stages.length ? (
        <p className="mt-2 hidden text-mono text-muted sm:block" aria-hidden="true">
          {stages
            .filter((s) => s.status === 'done' && s.detail)
            .slice(-1)
            .map((s) => `${s.label}: ${s.detail}`)}
        </p>
      ) : null}
    </div>
  )
}
