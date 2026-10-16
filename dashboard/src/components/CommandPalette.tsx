import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { useMeta, useScenarios, useTraces, useVerdicts } from '../api/hooks'
import { DECISION_DISPLAY, PALETTE, SHORTCUTS, SHORTCUTS_TITLE } from '../copy'
import { relative } from '../lib/format'
import { useTheme } from '../lib/theme'
import { Button } from './Button'
import { Dialog } from './Dialog'

interface Cmd {
  id: string
  group: string
  label: string
  hint?: string
  keywords?: string
  mono?: boolean
  run: () => void
}

/** Every word of the query must appear somewhere in the label, hint or keywords. */
export function matches(c: Pick<Cmd, 'label' | 'hint' | 'keywords' | 'group'>, query: string): boolean {
  const hay = `${c.label} ${c.hint ?? ''} ${c.keywords ?? ''} ${c.group}`.toLowerCase()
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((w) => hay.includes(w))
}

function useCommandList(close: () => void, openShortcuts: () => void): Cmd[] {
  const navigate = useNavigate()
  const { data: meta } = useMeta()
  const operator = meta?.public_mode === false
  const verdicts = useVerdicts()
  const scenarios = useScenarios()
  const traces = useTraces({}, operator)
  const [, toggleTheme] = useTheme()
  {
    const go = (to: string) => () => {
      close()
      navigate(to)
    }
    const out: Cmd[] = []
    const pages: [string, string][] = [
      ['Timeline', '/timeline'],
      ['Verdicts', '/verdicts'],
      ['Digest', '/digest'],
      ['Costs', '/costs'],
      ...(operator ? ([['Traces', '/traces'], ['Settings', '/settings'], ['Component kit', '/__kit']] as [string, string][]) : ([['Simulate', '/']] as [string, string][])),
    ]
    for (const [label, to] of pages) out.push({ id: `go:${to}`, group: PALETTE.groups.go!, label, run: go(to) })
    for (const v of verdicts.data?.items ?? [])
      out.push({
        id: `v:${v.verdict_id}`,
        group: PALETTE.groups.verdicts!,
        label: `${DECISION_DISPLAY[v.decision]} ${v.affected_skills.join(', ')}`,
        hint: relative(v.created_at),
        keywords: `${v.headline} ${v.verdict_id} ${v.decision}`,
        run: go(`/verdicts/${encodeURIComponent(v.verdict_id)}`),
      })
    if (meta?.simulate_enabled !== false)
      for (const s of scenarios.data ?? [])
        out.push({ id: `s:${s.scenario_id}`, group: PALETTE.groups.scenarios!, label: `Simulate ${s.skill}`, hint: s.learned, keywords: 'run scenario', mono: false, run: go(`/?run=${encodeURIComponent(s.scenario_id)}`) })
    const filters: [string, string][] = [
      ['Only verdicts', 'kind=verdicts'],
      ['Only blocked verdicts', 'kind=verdicts&decision=BLOCK'],
      ['Only held verdicts', 'kind=verdicts&decision=WARN'],
      ['Only passed verdicts', 'kind=verdicts&decision=PASS'],
      ['Only actions (merge, revert, roll back)', 'kind=actions'],
      ['Only blessings', 'kind=blessings'],
      ['Only edits', 'kind=edits'],
    ]
    for (const [label, q] of filters) out.push({ id: `t:${q}`, group: PALETTE.groups.timeline!, label, keywords: 'timeline filter', run: go(`/timeline?${q}`) })
    const skills = [...new Set((verdicts.data?.items ?? []).flatMap((v) => v.affected_skills))].sort()
    for (const sk of skills) out.push({ id: `k:${sk}`, group: PALETTE.groups.skills!, label: sk, hint: 'Timeline for this skill', keywords: 'skill', mono: true, run: go(`/timeline?skill=${encodeURIComponent(sk)}`) })
    for (const t of (operator ? traces.data?.items : null) ?? [])
      out.push({ id: `tr:${t.run_id}`, group: PALETTE.groups.traces!, label: t.task_label || t.task_input, hint: t.run_id, keywords: t.skills_invoked.join(' '), run: go(`/traces/${encodeURIComponent(t.run_id)}`) })
    out.push({
      id: 'a:theme',
      group: PALETTE.groups.actions!,
      label: 'Switch theme',
      keywords: 'dark light',
      run: () => {
        toggleTheme()
        close()
      },
    })
    out.push({
      id: 'a:keys',
      group: PALETTE.groups.actions!,
      label: SHORTCUTS_TITLE,
      keywords: 'help ?',
      run: () => {
        close()
        openShortcuts()
      },
    })
    return out
  }
}

/** ⌘K / Ctrl K: jump to a verdict, trace or skill, run a scenario, or filter the timeline. */
export function CommandPalette({ open, onClose, onShortcuts }: { open: boolean; onClose: () => void; onShortcuts: () => void }) {
  if (!open) return null
  return <PaletteBody onClose={onClose} onShortcuts={onShortcuts} />
}

function PaletteBody({ onClose, onShortcuts }: { onClose: () => void; onShortcuts: () => void }) {
  const [q, setQ] = useState('')
  const [index, setIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const all = useCommandList(onClose, onShortcuts)
  const shown = useMemo(() => (q.trim() ? all.filter((c) => matches(c, q)) : all.filter((c) => c.group !== PALETTE.groups.traces)).slice(0, 60), [all, q])
  const cur = Math.min(index, Math.max(0, shown.length - 1))
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') setIndex((i) => Math.min(shown.length - 1, i + 1))
    else if (e.key === 'ArrowUp') setIndex((i) => Math.max(0, i - 1))
    else if (e.key === 'Enter') shown[cur]?.run()
    else return
    e.preventDefault()
  }
  const heads = shown.map((c, i) => i === 0 || shown[i - 1]!.group !== c.group)
  return (
    <Dialog open title={PALETTE.title} hideTitle onClose={onClose} initialFocus={inputRef} position="top" width={600} testId="command-palette">
      <div className="flex items-center gap-3 border-b border-border px-4">
        <Search size={16} aria-hidden="true" className="shrink-0 text-muted" />
        <input
          ref={inputRef}
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setIndex(0)
          }}
          onKeyDown={onKey}
          placeholder={PALETTE.placeholder}
          aria-label={PALETTE.placeholder}
          role="combobox"
          aria-expanded="true"
          aria-controls={listId}
          aria-activedescendant={shown[cur] ? `${listId}-${cur}` : undefined}
          className="h-12 min-w-0 flex-1 bg-transparent text-h3 text-text outline-none"
        />
        <span className="kbd">Esc</span>
      </div>
      <ul id={listId} role="listbox" aria-label={PALETTE.title} className="max-h-[min(60vh,440px)] overflow-y-auto py-1">
        {shown.length === 0 ? <li className="px-4 py-6 text-body text-muted">{PALETTE.empty}</li> : null}
        {shown.map((c, i) => {
          const head = heads[i]
          return (
            <li key={c.id} role="presentation">
              {head ? (
                <p role="presentation" className="px-4 pt-3 pb-1 text-meta text-muted">
                  {c.group}
                </p>
              ) : null}
              <div
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === cur}
                onMouseMove={() => setIndex(i)}
                onClick={c.run}
                className={`mx-1 flex min-h-9 cursor-pointer items-center gap-3 rounded-[6px] px-3 py-1.5 ${i === cur ? 'bg-surface-2 text-text' : 'text-text'}`}
              >
                <span className={`min-w-0 flex-1 truncate ${c.mono ? 'font-mono text-mono' : 'text-body'}`}>{c.label}</span>
                {c.hint ? <span className="max-w-[45%] shrink-0 truncate text-meta text-muted">{c.hint}</span> : null}
              </div>
            </li>
          )
        })}
      </ul>
      <p className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border px-4 py-2 text-meta text-muted">
        <span>
          <span className="kbd">↑</span> <span className="kbd">↓</span> move
        </span>
        <span>
          <span className="kbd">↵</span> open
        </span>
        <span>
          <span className="kbd">?</span> shortcuts
        </span>
      </p>
    </Dialog>
  )
}

/** The `?` sheet: every shortcut, one line each. */
export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null)
  return (
    <Dialog
      open={open}
      title={SHORTCUTS_TITLE}
      onClose={onClose}
      initialFocus={closeRef}
      width={420}
      testId="shortcut-sheet"
      footer={
        <Button ref={closeRef} onClick={onClose}>
          Close
        </Button>
      }
    >
      <dl className="flex flex-col divide-y divide-border">
        {SHORTCUTS.map(([keys, what]) => (
          <div key={what} className="flex items-center justify-between gap-4 py-2">
            <dt className="text-body text-text">{what}</dt>
            <dd className="flex gap-1">
              {keys.map((k) => (
                <kbd key={k} className="kbd">
                  {k}
                </kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </Dialog>
  )
}
