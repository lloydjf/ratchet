import { useState, type ReactNode } from 'react'
import { ChevronRight, FileText, Filter } from 'lucide-react'
import { EVIDENCE } from '../copy'

export interface DiffLine {
  type: 'add' | 'del' | 'ctx' | 'hunk' | 'meta'
  text: string
  oldNo: number | null
  newNo: number | null
}
export interface DiffFile {
  path: string
  status: 'modified' | 'added' | 'deleted'
  additions: number
  deletions: number
  lines: DiffLine[]
}

/** Parse a unified (git) diff into files and numbered lines. */
export function parseUnifiedDiff(patch: string): DiffFile[] {
  const files: DiffFile[] = []
  let cur: DiffFile | null = null
  let oldNo = 0
  let newNo = 0
  for (const raw of patch.replace(/\r\n/g, '\n').split('\n')) {
    if (raw.startsWith('diff --git ')) {
      const m = raw.match(/ b\/(.+)$/)
      cur = { path: m?.[1] ?? raw.slice(11), status: 'modified', additions: 0, deletions: 0, lines: [] }
      files.push(cur)
      continue
    }
    if (!cur) {
      if (raw.startsWith('--- ') || raw.startsWith('+++ ') || raw.startsWith('@@')) {
        cur = { path: 'patch', status: 'modified', additions: 0, deletions: 0, lines: [] }
        files.push(cur)
      } else continue
    }
    if (raw.startsWith('new file mode')) {
      cur.status = 'added'
      continue
    }
    if (raw.startsWith('deleted file mode')) {
      cur.status = 'deleted'
      continue
    }
    if (raw.startsWith('index ') || raw.startsWith('similarity') || raw.startsWith('rename ')) continue
    if (raw.startsWith('--- ')) continue
    if (raw.startsWith('+++ ')) {
      const p = raw.slice(4).replace(/^b\//, '')
      if (p !== '/dev/null' && cur.path === 'patch') cur.path = p
      continue
    }
    if (raw.startsWith('@@')) {
      const m = raw.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@ ?(.*)$/)
      oldNo = m ? Number(m[1]) : 0
      newNo = m ? Number(m[2]) : 0
      cur.lines.push({ type: 'hunk', text: raw, oldNo: null, newNo: null })
      continue
    }
    if (raw.startsWith('\\')) continue
    if (raw.startsWith('+')) {
      cur.lines.push({ type: 'add', text: raw.slice(1), oldNo: null, newNo: newNo++ })
      cur.additions++
    } else if (raw.startsWith('-')) {
      cur.lines.push({ type: 'del', text: raw.slice(1), oldNo: oldNo++, newNo: null })
      cur.deletions++
    } else if (raw.startsWith(' ') || raw === '') {
      if (raw === '' && cur.lines.length === 0) continue
      cur.lines.push({ type: 'ctx', text: raw.slice(1), oldNo: oldNo++, newNo: newNo++ })
    }
  }
  // Drop a trailing empty context line produced by the final newline.
  for (const f of files) {
    const last = f.lines.at(-1)
    if (last && last.type === 'ctx' && last.text === '') f.lines.pop()
  }
  return files
}

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*)/g

/** Light markdown highlighting for SKILL.md lines: headings, lists, quotes, code, bold. */
export function highlightMarkdown(text: string): ReactNode {
  const heading = text.match(/^(#{1,6}\s)(.*)$/)
  if (heading)
    return (
      <>
        <span className="text-muted">{heading[1]}</span>
        <span className="font-bold text-text">{inline(heading[2] ?? '')}</span>
      </>
    )
  const list = text.match(/^(\s*(?:[-*+]|\d+\.)\s)(.*)$/)
  if (list)
    return (
      <>
        <span className="text-drift-ink">{list[1]}</span>
        {inline(list[2] ?? '')}
      </>
    )
  const quote = text.match(/^(>\s?)(.*)$/)
  if (quote)
    return (
      <>
        <span className="text-muted">{quote[1]}</span>
        <span className="italic">{inline(quote[2] ?? '')}</span>
      </>
    )
  const front = text.match(/^([a-z_]+:)(\s.*)$/)
  if (front)
    return (
      <>
        <span className="text-super-ink">{front[1]}</span>
        {front[2]}
      </>
    )
  return inline(text)
}

function inline(text: string): ReactNode {
  const parts = text.split(INLINE)
  return parts.map((p, i) => {
    if (p.startsWith('`') && p.endsWith('`') && p.length > 1)
      return (
        <span key={i} className="text-drift-ink">
          {p}
        </span>
      )
    if (p.startsWith('**') && p.endsWith('**') && p.length > 3)
      return (
        <span key={i} className="font-bold text-text">
          {p}
        </span>
      )
    return p
  })
}

interface DiffViewerProps {
  patch: string
  collapsedByDefault?: boolean
  className?: string
  onExpand?: () => void
  /** Pairs each hunk affected, by hunk id (`lib/hunks`); shows a count that filters the grid. */
  hunkCounts?: Map<string, number>
  activeHunk?: string | null
  onHunk?: (id: string | null) => void
}

/** Unified diff with line numbers, markdown highlighting, per-file collapse and per-hunk pair counts. */
export function DiffViewer({ patch, collapsedByDefault = false, className = '', onExpand, hunkCounts, activeHunk, onHunk }: DiffViewerProps) {
  const files = parseUnifiedDiff(patch)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  if (!files.length) return <p className="text-meta text-muted">No file changes.</p>
  const starts = files.map((_, i) => files.slice(0, i).reduce((a, f) => a + f.lines.filter((l) => l.type === 'hunk').length, 0))
  return (
    <div className={`flex flex-col gap-4 ${className}`} data-testid="diff-viewer">
      {files.map((f, fi) => {
        const isOpen = open[f.path] ?? !collapsedByDefault
        const id = `diff-${f.path.replace(/\W+/g, '-')}`
        const first = starts[fi]!
        const hunkIndex = f.lines.map((l, i) => (l.type === 'hunk' ? f.lines.slice(0, i).filter((x) => x.type === 'hunk').length : -1))
        return (
          <section key={f.path} className="border border-border bg-bg">
            <h3 className="m-0">
              <button
                type="button"
                aria-expanded={isOpen}
                aria-controls={id}
                onClick={() => {
                  setOpen((o) => ({ ...o, [f.path]: !isOpen }))
                  if (!isOpen) onExpand?.()
                }}
                className="flex min-h-10 w-full items-center gap-2 bg-surface-2 px-3 text-left hover:bg-[color-mix(in_srgb,var(--text)_4%,var(--surface-2))]"
              >
                <ChevronRight size={14} aria-hidden="true" className={`shrink-0 text-muted transition-transform duration-[160ms] ${isOpen ? 'rotate-90' : ''}`} />
                <FileText size={14} aria-hidden="true" className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate font-mono text-mono text-text">{f.path}</span>
                {f.status === 'added' ? <span className="text-meta text-muted">new file</span> : null}
                <span className="num shrink-0 font-mono text-mono">
                  <span className="text-pass-ink">+{f.additions}</span> <span className="text-block-ink">−{f.deletions}</span>
                </span>
                <span className="sr-only">{isOpen ? 'Collapse file' : 'Expand file'}</span>
              </button>
            </h3>
            {isOpen ? (
              <div id={id} className="border-t border-border font-mono text-mono" role="table" aria-label={`Changes in ${f.path}`}>
                {f.lines.map((l, i) => {
                  if (l.type === 'hunk') {
                    const hid = `${f.path}#${first + hunkIndex[i]!}`
                    const n = hunkCounts?.get(hid)
                    const active = activeHunk === hid
                    return (
                      <div key={i} role="row" className="flex items-center gap-3 border-b border-border/60 bg-surface px-3 py-1 text-muted" data-hunk={hid}>
                        <span role="cell" className="min-w-0 flex-1 truncate">
                          {l.text}
                        </span>
                        {n != null ? (
                          <span role="cell" className="shrink-0 font-sans">
                            {n > 0 && onHunk ? (
                              <button
                                type="button"
                                aria-pressed={active}
                                onClick={() => onHunk(active ? null : hid)}
                                aria-label={EVIDENCE.hunkFilter(n)}
                                className={`num inline-flex min-h-7 items-center gap-1.5 rounded-sm border px-2 text-meta ${active ? 'border-accent bg-surface-2 text-text' : 'border-border-strong text-text hover:bg-surface-2'}`}
                                data-testid="hunk-pairs"
                              >
                                <Filter size={12} aria-hidden="true" />
                                {EVIDENCE.hunkPairs(n)}
                              </button>
                            ) : (
                              <span className="text-meta text-muted">{EVIDENCE.hunkPairs(n)}</span>
                            )}
                          </span>
                        ) : null}
                      </div>
                    )
                  }
                  return (
                    <div
                      key={i}
                      role="row"
                      data-line={l.type}
                      className={`grid grid-cols-[2.75rem_2.75rem_1.25rem_1fr] ${l.type === 'add' ? 'bg-[var(--diff-add)]' : l.type === 'del' ? 'bg-[var(--diff-del)]' : ''}`}
                    >
                      <span role="cell" className="num select-none border-r border-border/60 pr-2 text-right text-muted">
                        {l.oldNo ?? ''}
                      </span>
                      <span role="cell" className="num select-none border-r border-border/60 pr-2 text-right text-muted">
                        {l.newNo ?? ''}
                      </span>
                      <span role="cell" aria-label={l.type === 'add' ? 'added' : l.type === 'del' ? 'removed' : undefined} className={`select-none text-center ${l.type === 'add' ? 'text-pass-ink' : l.type === 'del' ? 'text-block-ink' : 'text-muted'}`}>
                        {l.type === 'add' ? '+' : l.type === 'del' ? '−' : ''}
                      </span>
                      <span role="cell" className="min-w-0 whitespace-pre-wrap break-words py-px pr-3 text-text">
                        {highlightMarkdown(l.text) || ' '}
                      </span>
                    </div>
                  )
                })}
              </div>
            ) : null}
          </section>
        )
      })}
    </div>
  )
}
