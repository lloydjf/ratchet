import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ExternalLink } from 'lucide-react'
import { useCosts, useVerdict } from '../api/hooks'
import type { CostsRange } from '../api/client'
import type { CostTile, CostsView } from '../api/types'
import { TIERS } from '../api/types'
import { track } from '../analytics'
import { CACHE_SHARE, cacheShareLine, EMPTY, overVerdicts, TIER_NAME, TIER_SHORT } from '../copy'
import { savingsLine } from '../components/CostPanel'
import { EmptyState } from '../components/EmptyState'
import { ErrorState } from '../components/ErrorState'
import { SkeletonRows } from '../components/Skeleton'
import { TierBadge } from '../components/TierBadge'
import { UsdTiers } from '../components/UsdTiers'
import { dateTime, ratioLabel, shortDay, time, sumTiers, tiersWithSpend, tokens, usd } from '../lib/format'
import { useDocumentTitle } from '../lib/useDocumentTitle'
import { useMediaQuery } from '../lib/useMediaQuery'
import { customRange, DEFAULT_PRESET, editRange, fillDays, PRESET_LABEL, presetRange, rangeInputs, type RangePreset } from '../lib/range'

/** One hairline row per window: name, total with its tiers, the three tiers on one scale, cache share. */
function Tile({ title, tile, footer, subtitle, testId }: { title: string; tile: CostTile; footer?: ReactNode; subtitle?: string; testId?: string }) {
  const total = sumTiers(tile.usd_by_tier)
  const max = Math.max(...TIERS.map((t) => tile.usd_by_tier[t] ?? 0), 0.000001)
  return (
    <section className="grid grid-cols-1 gap-x-8 gap-y-3 px-6 py-4 max-md:px-4 lg:grid-cols-[11rem_10rem_minmax(0,1fr)]" aria-label={title} data-testid={testId}>
      <div>
        <h2 className="heading-sm">{title}</h2>
        {subtitle ? <p className="num mt-0.5 text-meta text-muted">{subtitle}</p> : null}
      </div>
      <p className="num flex flex-wrap items-baseline gap-x-2 text-text lg:flex-col lg:gap-y-0.5" data-testid="tile-total">
        <span className="font-mono text-h3 font-medium">{usd(total)}</span>
        <span className="text-meta text-muted">({tiersWithSpend(tile.usd_by_tier)})</span>
      </p>
      <div className="flex flex-col gap-2">
        <ul className="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-3">
          {TIERS.map((t) => {
            const v = tile.usd_by_tier[t] ?? 0
            return (
              <li key={t} className="flex flex-col gap-1 text-meta">
                <span className="flex items-baseline justify-between gap-2">
                  <span style={{ color: `var(--${t}-ink)` }}>{TIER_SHORT[t]}</span>
                  <span className="num">
                    <span className="font-mono text-text">{usd(v)}</span> <span className="text-muted">({t})</span>
                  </span>
                </span>
                <span className="block h-[3px] bg-border" aria-hidden="true">
                  <span className="block h-full" style={{ width: `${v > 0 ? Math.max(2, (v / max) * 100) : 0}%`, background: `var(--${t})` }} />
                </span>
                <span className="num text-muted">
                  <span className="font-mono">{tokens(tile.tokens_by_tier[t])}</span> tokens
                </span>
              </li>
            )
          })}
        </ul>
        <p className="num text-meta text-muted" title={CACHE_SHARE.hint} data-testid="cache-share">
          {cacheShareLine(tile)}
        </p>
        {footer ? <div>{footer}</div> : null}
      </div>
    </section>
  )
}

function ByDay({ days }: { days: CostsView['by_day'] }) {
  const [table, setTable] = useState(false)
  // Label every day up to two weeks; beyond that, every k-th day so labels never collide.
  const narrow = useMediaQuery('(max-width: 479px)')
  const every = Math.max(narrow ? 3 : 1, Math.ceil(days.length / 14))
  const max = Math.max(...days.map((d) => sumTiers(d.usd_by_tier)), 0.000001)
  const H = 140
  return (
    <section className="card p-4" aria-labelledby="byday-title">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 id="byday-title" className="text-h3 font-semibold text-text">Judging spend by day</h2>
        <div className="flex items-center gap-4">
          <ul className="flex gap-3 text-mono text-muted" aria-label="Legend">
            {TIERS.map((t) => (
              <li key={t} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="h-2.5 w-2.5 rounded-[2px]" style={{ background: `var(--${t})` }} />
                {TIER_SHORT[t]}
              </li>
            ))}
          </ul>
          <button type="button" onClick={() => setTable((x) => !x)} className="inline-flex min-h-11 items-center rounded-sm px-2 text-meta font-medium text-accent hover:bg-surface-2" aria-pressed={table}>
            {table ? 'Show chart' : 'Show as table'}
          </button>
        </div>
      </div>
      {table ? (
        <table className="mt-3 w-full text-left text-meta">
          <thead className="text-mono text-muted">
            <tr><th scope="col" className="py-2">Day</th>{TIERS.map((t) => <th key={t} scope="col" className="py-2 text-right">{TIER_SHORT[t]}</th>)}<th scope="col" className="py-2 text-right">Total</th></tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.day} className="border-t border-border">
                <td className="py-2 text-text">{shortDay(d.day)}</td>
                {TIERS.map((t) => <td key={t} className="num py-2 text-right font-mono text-text">{usd(d.usd_by_tier[t] ?? 0)}</td>)}
                <td className="num py-2 text-right font-mono font-semibold text-text">{usd(sumTiers(d.usd_by_tier))}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className={`mt-4 grid ${days.length > 31 ? 'gap-px' : days.length > 14 ? 'gap-1' : 'gap-2'}`} style={{ gridTemplateColumns: `repeat(${Math.max(1, days.length)}, minmax(0, 1fr))` }} role="list" aria-label="Spend per day">
          {days.map((d, i) => {
            const total = sumTiers(d.usd_by_tier)
            const label = `${shortDay(d.day)}: ${usd(total)} — ${TIERS.map((t) => `${TIER_SHORT[t]} ${usd(d.usd_by_tier[t] ?? 0)}`).join(', ')}`
            return (
              <div key={d.day} role="listitem" className="group flex flex-col items-center gap-2" tabIndex={0} aria-label={label} title={label}>
                <span className="num font-mono text-[11px] leading-[14px] text-muted opacity-0 group-hover:opacity-100 group-focus:opacity-100">{usd(total)}</span>
                <div className="flex w-full max-w-10 flex-col-reverse gap-[2px]" style={{ height: H }} aria-hidden="true">
                  {TIERS.map((t) => {
                    const v = d.usd_by_tier[t] ?? 0
                    if (!v) return null
                    return <span key={t} className="block w-full" style={{ height: Math.max(2, (v / max) * (H - 8)), background: `var(--${t})` }} />
                  })}
                  {!total ? <span className="block h-px w-full bg-border" /> : null}
                </div>
                <span className={`num text-mono whitespace-nowrap text-muted ${i % every === 0 ? '' : 'invisible'}`}>{shortDay(d.day)}</span>
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

const PRESETS: Exclude<RangePreset, 'custom'>[] = ['today', '7d', '14d', 'edit']

function rangeLabel(preset: RangePreset, r: CostsRange): string {
  if (preset !== 'custom') return preset === 'edit' ? PRESET_LABEL.edit : preset === 'today' ? 'Today' : `Last ${PRESET_LABEL[preset]}`
  const { from, to } = rangeInputs(r)
  return from === to ? shortDay(from) : `${shortDay(from)} – ${shortDay(to)}`
}

/** The window as people read it: whole days inclusive, or exact times for one edit. */
function windowSpan(preset: RangePreset, w: CostsRange): string {
  if (preset === 'edit') return `${dateTime(w.from)} – ${time(w.to)}`
  const { from, to } = rangeInputs(w)
  return from === to ? shortDay(from) : `${shortDay(from)} – ${shortDay(to)}`
}

/** Date-range picker: preset chips plus a from/to pair (inclusive days). */
function RangePicker({ preset, range, editWindow, onPick }: { preset: RangePreset; range: CostsRange; editWindow: CostsRange | null; onPick: (p: RangePreset, r: CostsRange) => void }) {
  const inputs = rangeInputs(range)
  const [from, setFrom] = useState(inputs.from)
  const [to, setTo] = useState(inputs.to)
  const [invalid, setInvalid] = useState(false)
  // Keep the inputs in step when a chip picks the window.
  const key = `${range.from}|${range.to}`
  const [shown, setShown] = useState(key)
  if (shown !== key) {
    setShown(key)
    setFrom(inputs.from)
    setTo(inputs.to)
    setInvalid(false)
  }
  const applyCustom = (f: string, t: string) => {
    const r = customRange(f, t)
    setInvalid(!r)
    if (r) onPick('custom', r)
  }
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-end sm:justify-between" data-testid="range-picker">
      <div role="group" aria-label="Date range" className="flex flex-wrap gap-1">
        {PRESETS.map((p) => {
          const disabled = p === 'edit' && !editWindow
          return (
            <button
              key={p}
              type="button"
              aria-pressed={preset === p}
              disabled={disabled}
              onClick={() => {
                const r = p === 'edit' ? editWindow : presetRange(p)
                if (r) onPick(p, r)
              }}
              className={`min-h-11 rounded-sm border px-3 text-meta font-medium disabled:cursor-not-allowed disabled:opacity-50 ${preset === p ? 'border-border-strong bg-surface-2 text-text' : 'border-transparent text-muted hover:text-text'}`}
              data-preset={p}
            >
              {PRESET_LABEL[p]}
            </button>
          )
        })}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-mono text-muted">
          From
          <input type="date" value={from} max={to} onChange={(e) => { setFrom(e.target.value); applyCustom(e.target.value, to) }} className="min-h-11 rounded-sm border border-border-strong bg-surface px-2 font-mono text-meta text-text" data-testid="range-from" />
        </label>
        <label className="flex flex-col gap-1 text-mono text-muted">
          To
          <input type="date" value={to} min={from} onChange={(e) => { setTo(e.target.value); applyCustom(from, e.target.value) }} className="min-h-11 rounded-sm border border-border-strong bg-surface px-2 font-mono text-meta text-text" data-testid="range-to" />
        </label>
        {invalid ? <p role="alert" className="basis-full text-meta text-warn-ink">Pick a start on or before the end, at most 366 days apart.</p> : null}
      </div>
    </div>
  )
}

export default function CostsPage() {
  useDocumentTitle(['Costs'])
  const [{ preset, range }, setPick] = useState<{ preset: RangePreset; range: CostsRange }>(() => ({ preset: DEFAULT_PRESET, range: presetRange('14d') }))
  const q = useCosts(range)
  const lastVerdictId = q.data?.last_edit?.verdict_id ?? undefined
  const last = useVerdict(lastVerdictId)
  const editWindow = useMemo(() => {
    const v = last.data
    return v?.verdict ? editRange(v.edit.detected_at, v.verdict.created_at) : null
  }, [last.data])
  useEffect(() => {
    track('demo.evidence_open', { which: 'cost' })
  }, [])
  if (q.isLoading) return <SkeletonRows rows={4} label="Loading costs" />
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  const c = q.data
  const win = c.window ?? null
  const label = rangeLabel(preset, range)
  const empty = sumTiers(c.week.usd_by_tier) === 0 && sumTiers(c.today.usd_by_tier) === 0 && sumTiers(win?.usd_by_tier ?? {}) === 0
  const cf = c.counterfactual
  const saved = savingsLine(cf.actual_usd, cf.all_ultra_usd)
  const cfByTier = cf.actual_usd_by_tier && Object.keys(cf.actual_usd_by_tier).length ? cf.actual_usd_by_tier : null
  const lastTotal = c.last_edit ? sumTiers(c.last_edit.usd_by_tier) : 0
  const days = fillDays(c.by_day, win ? { from: win.from, to: win.to } : null, (day) => ({ day, usd_by_tier: {} }))
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-h1 font-semibold text-text">Costs</h1>
        <p className="text-body text-muted">What judging cost, by tier. {TIER_NAME.nano} screens every pair; {TIER_NAME.super} and {TIER_NAME.ultra} only see what is left.</p>
      </header>
      <RangePicker preset={preset} range={range} editWindow={editWindow} onPick={(p, r) => setPick({ preset: p, range: r })} />
      {empty ? (
        <EmptyState title={EMPTY.costs} action={{ label: 'Simulate a scenario', to: '/' }} />
      ) : (
        <>
          <div className="stack">
          {win ? (
            <Tile
              title={label}
              subtitle={windowSpan(preset, win)}
              tile={win}
              testId="window-tile"
            />
          ) : null}
            <Tile title="Today" tile={c.today} />
            <Tile title="This week" tile={c.week} />
            {c.last_edit ? (
              <Tile
                title="This edit"
                tile={c.last_edit}
                footer={
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-meta">
                    {savingsLine(lastTotal, c.last_edit.counterfactual_usd) ? <p className="text-pass-ink">{savingsLine(lastTotal, c.last_edit.counterfactual_usd)}</p> : null}
                    {c.last_edit.verdict_id ? (
                      <Link to={`/verdicts/${encodeURIComponent(c.last_edit.verdict_id)}`} className="link inline-flex min-h-8 items-center gap-1">
                        Open its verdict <ArrowRight size={14} aria-hidden="true" />
                      </Link>
                    ) : null}
                  </div>
                }
              />
            ) : null}
          </div>

          <section className="card flex flex-col gap-2 p-4 sm:flex-row sm:items-center sm:justify-between" aria-label="Counterfactual" data-testid="counterfactual">
            <p className="num text-body text-text">
              The cascade spent{' '}
              {cfByTier ? <UsdTiers map={cfByTier} className="font-semibold" /> : <span className="font-mono font-semibold">{usd(cf.actual_usd)}</span>}{' '}
              <span data-testid="counterfactual-n">{overVerdicts(cf.n_verdicts)}</span>. Judging everything with {TIER_NAME.ultra} would have cost{' '}
              <span className="font-mono font-semibold">{usd(cf.all_ultra_usd)}</span> (ultra), {ratioLabel(cf.saved_ratio)}× more.
            </p>
            {saved ? <p className="shrink-0 text-meta font-medium text-pass-ink">{saved}</p> : null}
          </section>

          <ByDay days={days} />

          <section className="flex flex-col gap-3" aria-labelledby="purpose-title">
            <h2 id="purpose-title" className="text-h3 font-semibold text-text">By purpose{win ? `: ${label.charAt(0).toLowerCase()}${label.slice(1)}` : ' this week'}</h2>
            <div className="overflow-x-auto border border-border bg-surface">
              <table className="w-full min-w-[560px] border-collapse text-left">
                <thead className="text-meta text-muted">
                  <tr>
                    <th scope="col" className="px-4 py-2.5 font-medium">Purpose</th>
                    <th scope="col" className="px-2 py-2.5 font-medium">Tier</th>
                    <th scope="col" className="px-2 py-2.5 text-right font-medium">Calls</th>
                    <th scope="col" className="px-2 py-2.5 text-right font-medium">Tokens</th>
                    <th scope="col" className="px-4 py-2.5 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {c.by_purpose.length === 0 ? (
                    <tr className="border-t border-border">
                      <td colSpan={5} className="px-4 py-3 text-meta text-muted">No model calls in this range.</td>
                    </tr>
                  ) : null}
                  {c.by_purpose.map((p) => (
                    <tr key={`${p.purpose}-${p.tier}`} className="border-t border-border">
                      <td className="px-4 py-2.5 text-body text-text">{PURPOSE_LABEL[p.purpose] ?? p.purpose}</td>
                      <td className="px-2 py-2.5"><span className="inline-flex items-center gap-2 text-meta text-text"><TierBadge tier={p.tier} plain size="sm" />{TIER_SHORT[p.tier]}</span></td>
                      <td className="num px-2 py-2.5 text-right font-mono text-meta text-text">{p.calls}</td>
                      <td className="num px-2 py-2.5 text-right font-mono text-meta text-muted">{tokens(p.tokens)}</td>
                      <td className="num px-4 py-2.5 text-right font-mono text-meta text-text">{usd(p.usd)} <span className="font-sans text-muted">({p.tier})</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-meta text-muted">Agent replays run on the same tier as the original task, so any difference is the skill’s, not the model’s.</p>
          </section>

          {c.bench_report_url ? (
            <a href={c.bench_report_url} target="_blank" rel="noreferrer noopener" className="link inline-flex min-h-11 items-center gap-1.5 self-start text-meta font-medium">
              SkillRegress-Bench: cost per verdict, cascade vs all-Ultra <ExternalLink size={14} aria-hidden="true" />
              <span className="sr-only">(opens in a new tab)</span>
            </a>
          ) : null}
        </>
      )}
    </div>
  )
}

const PURPOSE_LABEL: Record<string, string> = {
  agent: 'Agent replays',
  screen: 'Screening (Level 1)',
  check: 'Checking (Level 2)',
  adjudicate: 'Adjudicating (Level 3)',
  extract: 'Extracting world facts',
  narrative: 'Writing summaries',
  bench: 'Benchmark',
  embed: 'Embeddings',
}



