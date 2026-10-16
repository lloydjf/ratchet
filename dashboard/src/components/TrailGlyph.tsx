import type { PairVerdict, Tier } from '../api/types'
import { TIERS } from '../api/types'
import { CONTROL, LEVEL0_LABEL, OPINION_LABEL, SKIPPED_TIER, TIER_NAME } from '../copy'
import { level0Copy } from '../lib/pairs'
import { settledStage } from '../lib/sieve'
import { OUTCOME_COLOR } from './Sieve'
import { OpinionMark } from './Marks'
import { Tooltip } from './Tooltip'

const STAGES = ['L0', 'N', 'S', 'U'] as const
const STAGE_NAME = ['Level 0', 'Nano', 'Super', 'Ultra'] as const
const VERDICT_INK = { equivalent: 'text-pass-ink', improved: 'text-pass-ink', regressed: 'text-block-ink', uncertain: 'text-warn-ink' } as const

type TrailPair = Pick<PairVerdict, 'opinions' | 'decided_by' | 'drift' | 'outcome' | 'level0'>

/** Which of the four stages saw the pair, and the stage that decided it. */
export function trailStages(p: TrailPair): { consulted: boolean[]; deciding: number } {
  const deciding = p.decided_by === 'infra' ? -1 : settledStage(p)
  const tiers = new Set(p.opinions.map((o) => o.tier))
  const consulted = [p.decided_by !== 'infra', ...TIERS.map((t) => tiers.has(t))]
  return { consulted, deciding }
}

function trailLabel(p: TrailPair): string {
  const { consulted, deciding } = trailStages(p)
  const parts = STAGE_NAME.map((n, i) => {
    if (i === 0) return consulted[0] ? `${n} checked` : `${n} not run`
    const o = p.opinions.find((x) => x.tier === TIERS[i - 1])
    return o ? `${n} ${o.verdict} ${Math.round(o.confidence * 100)}%` : `${n} not needed`
  })
  const by = p.decided_by === 'drift' ? 'Tavily drift check' : p.decided_by === 'control' ? 'the control replay' : deciding >= 0 ? STAGE_NAME[deciding] : 'nothing (never replayed)'
  return `Judge trail: ${parts.join(', ')}. Decided by ${by}.`
}

function Popover({ p }: { p: TrailPair }) {
  const l0 = level0Copy(p as PairVerdict)
  return (
    <div className="flex w-[300px] max-w-full flex-col gap-2">
      <div>
        <p className="font-medium text-text">Level 0</p>
        <p className="text-muted">{p.decided_by === 'infra' ? 'Not run: the replay did not finish.' : l0 ?? (p.level0.changed_args.length ? LEVEL0_LABEL.changed : LEVEL0_LABEL.none)}</p>
      </div>
      {TIERS.map((t: Tier) => {
        const o = p.opinions.find((x) => x.tier === t)
        return (
          <div key={t}>
            <p className="flex items-baseline justify-between gap-2">
              <span className="font-medium text-text">{TIER_NAME[t]}</span>
              {o ? (
                <span className={`num inline-flex items-center gap-1 ${VERDICT_INK[o.verdict]}`}>
                  <OpinionMark verdict={o.verdict} /> {OPINION_LABEL[o.verdict]} · {Math.round(o.confidence * 100)}%
                </span>
              ) : null}
            </p>
            <p className="text-muted">{o ? (o.reasons[0] ?? '') : SKIPPED_TIER}</p>
          </div>
        )
      })}
      {p.drift.length ? (
        <p className="text-muted">
          <span className="font-medium text-text">Tavily · </span>
          {p.drift.some((d) => d.changed && d.status === 'checked') ? 'a fact this task depends on changed.' : p.drift.every((d) => d.status === 'unavailable') ? 'could not verify whether the world changed.' : 'the facts this task depends on did not change.'}
        </p>
      ) : null}
      {p.decided_by === 'control' ? <p className="text-warn-ink">{CONTROL.trailTip}.</p> : null}
    </div>
  )
}

/**
 * The judge trail as one glyph: four segments, Level 0 · Nano · Super · Ultra, filled up to
 * the stage that decided the pair in its outcome colour, hollow where the cascade did not
 * need to go. Hover or focus opens each opinion's verdict, confidence and first reason.
 */
export function TrailGlyph({ p, className = '' }: { p: TrailPair; className?: string }) {
  const { consulted, deciding } = trailStages(p)
  const color = OUTCOME_COLOR[p.outcome]
  const W = 18
  const GAP = 3
  const width = STAGES.length * W + (STAGES.length - 1) * GAP
  return (
    <Tooltip content={<Popover p={p} />} side="top" align="end">
      {(t) => (
        <button type="button" className={`group inline-flex min-h-7 cursor-help items-center gap-2 rounded-sm px-1 hover:bg-surface-2 ${className}`} data-testid="trail-glyph" data-deciding={deciding} {...t}>
          <svg width={width} height={19} aria-hidden="true" className="block shrink-0">
            {STAGES.map((s, i) => {
              const x = i * (W + GAP)
              const filled = i <= deciding && (consulted[i] || i === deciding)
              return (
                <g key={s}>
                  <rect x={x + 0.5} y={0.5} width={W - 1} height={6} fill={filled ? color : 'none'} stroke={filled ? color : 'var(--border-strong)'} strokeDasharray={filled ? undefined : '2 2'} />
                  <text x={x + W / 2} y={18} textAnchor="middle" className="font-mono" fontSize="10" fill={i === deciding ? 'var(--text)' : 'var(--text-muted)'}>
                    {s}
                  </text>
                </g>
              )
            })}
          </svg>
          <span className="sr-only">{trailLabel(p)}</span>
          {p.drift.length ? (
            <span aria-hidden="true" className="font-mono text-meta text-drift-ink">
              T
            </span>
          ) : null}
          {p.decided_by === 'control' ? (
            <span aria-hidden="true" title={CONTROL.trailTip} data-testid="trail-control" className="font-mono text-meta text-warn-ink">
              C
            </span>
          ) : null}
        </button>
      )}
    </Tooltip>
  )
}
