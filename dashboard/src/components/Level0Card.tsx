import type { DecidedBy, Level0Finding, PairOutcome, ToolCall } from '../api/types'
import { HARD_SIDE_EFFECTS } from '../api/types'
import { BADGE, LEVEL0, outputSimilarityCopy } from '../copy'
import { diffPaths } from '../lib/align'
import { hasStructuralFinding, nanoGatedByLevel0 } from '../lib/pairs'

const show = (v: unknown) => (v === undefined ? 'missing' : typeof v === 'string' ? v : JSON.stringify(v))

function argsSummary(a: Record<string, unknown>): string {
  return Object.entries(a)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' · ')
}

function Call({ c, onStep }: { c: ToolCall; onStep?: (id: string) => void }) {
  const hard = HARD_SIDE_EFFECTS.has(c.side_effect)
  return (
    <li className="flex flex-col gap-1 py-2.5 sm:flex-row sm:items-center sm:gap-3">
      <span className="flex shrink-0 items-center gap-2">
        {onStep ? (
          <button type="button" onClick={() => onStep(c.step_id)} className="inline-flex min-h-8 items-center rounded-sm border border-border-strong bg-bg px-2 font-mono text-mono text-accent hover:bg-surface-2" aria-label={`Show step ${c.step_id}`}>
            {c.step_id}
          </button>
        ) : null}
        <span className="font-mono text-meta font-semibold text-text">{c.tool}</span>
        <span className={`inline-flex h-6 items-center rounded-sm border px-2 font-mono text-mono ${hard ? 'tint-block' : 'tint-warn'}`}>{c.side_effect}</span>
        {c.blocked ? <span className="inline-flex h-6 items-center rounded-sm border px-2 text-mono font-semibold tint-block">{BADGE.blocked}</span> : null}
      </span>
      <span className="min-w-0 truncate font-mono text-mono text-muted" title={argsSummary(c.args_norm)}>
        {argsSummary(c.args_norm)}
      </span>
    </li>
  )
}

interface Level0CardProps {
  finding: Level0Finding
  /** The pair's `decided_by`: says when Level 0 kept Nano from clearing it alone (ADR 0024). */
  decidedBy?: DecidedBy
  /** A pair that could not be replayed has no final output to compare. */
  outcome?: PairOutcome
  onStep?: (id: string) => void
}

/**
 * Level 0: the actions the replay took that the original did not, and how alike the two
 * final outputs are (no model involved).
 */
export function Level0Card({ finding, decidedBy, outcome, onStep }: Level0CardProps) {
  const { new_calls, missing_calls, changed_args } = finding
  const structural = hasStructuralFinding({ level0: finding })
  const failed = outcome === 'failed'
  if (!structural && failed) return null
  const gated = decidedBy ? nanoGatedByLevel0({ level0: finding, decided_by: decidedBy }) : false
  return (
    <section className={`card p-4 ${finding.hard_fail ? 'border-[color-mix(in_srgb,var(--block)_45%,var(--border))]' : ''}`} aria-labelledby="l0-title" data-testid="level0-card">
      <h2 id="l0-title" className="text-body font-semibold text-text">
        {LEVEL0.title}
      </h2>
      {!structural ? <p className="mt-2 text-meta text-muted">{LEVEL0.noActionChange}</p> : null}
      {new_calls.length ? (
        <>
          <p className="mt-2 text-meta text-muted">New in the replay ({new_calls.length})</p>
          <ul className="divide-y divide-border">
            {new_calls.map((c) => (
              <Call key={c.step_id} c={c} onStep={onStep} />
            ))}
          </ul>
        </>
      ) : null}
      {changed_args.length ? (
        <>
          <p className="mt-2 text-meta text-muted">Same call, different arguments ({changed_args.length})</p>
          <ul className="divide-y divide-border">
            {changed_args.map(([g, r]) => (
              <li key={r.step_id} className="py-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  {onStep ? (
                    <button type="button" onClick={() => onStep(r.step_id)} className="inline-flex min-h-8 items-center rounded-sm border border-border-strong bg-bg px-2 font-mono text-mono text-accent hover:bg-surface-2" aria-label={`Show step ${r.step_id}`}>
                      {r.step_id}
                    </button>
                  ) : null}
                  <span className="font-mono text-meta font-semibold text-text">{r.tool}</span>
                  <span className="text-mono text-muted">{diffPaths(g.args_norm, r.args_norm, '', [], 99).length} argument values differ</span>
                </div>
                <ul className="mt-1.5 flex flex-col gap-1 font-mono text-mono">
                  {diffPaths(g.args_norm, r.args_norm).map((d) => (
                    <li key={d.path} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 rounded-sm bg-surface-2 px-2 py-1">
                      <span className="text-muted">{d.path}</span>
                      <span className="rounded-[3px] bg-[var(--diff-del)] px-1 text-text">
                        <span className="sr-only">was </span>
                        {show(d.before)}
                      </span>
                      <span aria-hidden="true" className="text-muted">
                        →
                      </span>
                      <span className={`rounded-[3px] px-1 ${d.after === undefined ? 'border border-dashed border-border-strong text-block-ink' : 'bg-[var(--diff-add)] text-text'}`}>
                        <span className="sr-only">now </span>
                        {show(d.after)}
                      </span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {missing_calls.length ? (
        <>
          <p className="mt-2 text-meta text-muted">Missing from the replay ({missing_calls.length})</p>
          <ul className="divide-y divide-border">
            {missing_calls.map((c) => (
              <Call key={c.step_id} c={c} />
            ))}
          </ul>
        </>
      ) : null}
      {!failed || gated ? (
        <div className="mt-3 flex flex-col gap-1 border-t border-border pt-3 text-meta">
          {!failed ? (
            <p className="num text-text" data-testid="level0-output">
              {outputSimilarityCopy(finding.output_similarity)}
            </p>
          ) : null}
          {gated ? (
            <p className="text-muted" data-testid="level0-nano-gated">
              {LEVEL0.nanoGated}
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
