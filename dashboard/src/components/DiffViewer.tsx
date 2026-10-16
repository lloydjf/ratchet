import { useState, type ReactNode } from 'react'
import { ChevronRight, FileText } from 'lucide-react'

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
        <span key={i} className="rounded-[3px] bg-bg/60 px-0.5 text-drift-ink">
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
}

/** Unified diff with line numbers, markdown highlighting, and per-file collapse. */
export function DiffViewer({ patch, collapsedByDefault = false, className = '', onExpand }: DiffViewerProps) {
  const files = parseUnifiedDiff(patch)
  const [open, setOpen] = useState<Record<string, boolean>>({})
  if (!files.length) return <p className="text-meta text-muted">No file changes.</p>
  return (
    <div className={`flex flex-col gap-3 ${className}`} data-testid="diff-viewer">
      {files.map((f) => {
        const isOpen = open[f.path] ?? !collapsedByDefault
        const id = `diff-${f.path.replace(/\W+/g, '-')}`
        return (
          <section key={f.path} className="overflow-hidden rounded-card border border-border bg-bg">
            <h3 className="m-0">
              <button
                type="button"
                aria-expanded={isOpen}
                aria-controls={id}
                onClick={() => {
                  setOpen((o) => ({ ...o, [f.path]: !isOpen }))
                  if (!isOpen) onExpand?.()
                }}
                className="flex min-h-11 w-full items-center gap-2 bg-surface px-3 text-left hover:bg-surface-2"
              >
                <ChevronRight size={16} aria-hidden="true" className={`shrink-0 text-muted transition-transform duration-[160ms] ${isOpen ? 'rotate-90' : ''}`} />
                <FileText size={16} aria-hidden="true" className="shrink-0 text-muted" />
                <span className="min-w-0 flex-1 truncate font-mono text-mono text-text">{f.path}</span>
                {f.status === 'added' ? <span className="text-mono text-muted">new file</span> : null}
                <span className="num shrink-0 font-mono text-mono">
                  <span className="text-pass-ink">+{f.additions}</span> <span className="text-block-ink">−{f.deletions}</span>
                </span>
                <span className="sr-only">{isOpen ? 'Collapse file' : 'Expand file'}</span>
              </button>
            </h3>
            {isOpen ? (
              <div id={id} className="border-t border-border font-mono text-mono" role="table" aria-label={`Changes in ${f.path}`}>
                {f.lines.map((l, i) =>
                  l.type === 'hunk' ? (
                    <div key={i} role="row" className="flex bg-surface px-3 py-1 text-muted">
                      <span role="cell" className="truncate">
                        {l.text}
                      </span>
                    </div>
                  ) : (
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
                        {highlightMarkdown(l.text) || ' '}
                      </span>
                    </div>
                  ),
                )}
              </div>
            ) : null}
          </section>
        )
      })}
    </div>
  )
}
