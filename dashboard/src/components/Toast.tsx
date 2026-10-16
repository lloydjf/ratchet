import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { X } from 'lucide-react'

export type ToastTone = 'neutral' | 'pass' | 'warn' | 'block'
export interface ToastInput {
  message: string
  tone?: ToastTone
  link?: { label: string; to: string }
}
interface ToastItem extends ToastInput {
  id: number
}

const TOAST_MS = 6000
const MAX = 3

const Ctx = createContext<(t: ToastInput) => void>(() => {})

export const useToast = () => useContext(Ctx)

const TONE: Record<ToastTone, string> = {
  neutral: 'border-border-strong',
  pass: 'border-[color-mix(in_srgb,var(--pass)_60%,var(--surface))]',
  warn: 'border-[color-mix(in_srgb,var(--warn)_60%,var(--surface))]',
  block: 'border-[color-mix(in_srgb,var(--block)_60%,var(--surface))]',
}
const GLYPH: Record<ToastTone, string> = { neutral: '•', pass: '✓', warn: '!', block: '✕' }
const INK: Record<ToastTone, string> = { neutral: 'text-muted', pass: 'text-pass-ink', warn: 'text-warn-ink', block: 'text-block-ink' }

export function Toast({ item, onDismiss }: { item: ToastItem; onDismiss: (id: number) => void }) {
  useEffect(() => {
    const t = setTimeout(() => onDismiss(item.id), TOAST_MS)
    return () => clearTimeout(t)
  }, [item.id, onDismiss])
  const tone = item.tone ?? 'neutral'
  return (
    <li className={`anim-toast pointer-events-auto flex w-full items-start gap-3 rounded-card border bg-surface-2 py-2 pr-2 pl-4 ${TONE[tone]}`} data-testid="toast" data-tone={tone}>
      <span aria-hidden="true" className={`mt-2.5 font-mono text-meta font-bold ${INK[tone]}`}>
        {GLYPH[tone]}
      </span>
      <div className="min-w-0 flex-1 py-2">
        <p className="text-body text-text">{item.message}</p>
        {item.link ? (
          <Link to={item.link.to} className="link mt-1 inline-block text-meta font-medium">
            {item.link.label}
          </Link>
        ) : null}
      </div>
      <button type="button" onClick={() => onDismiss(item.id)} className="inline-flex min-h-11 items-center gap-1 rounded-sm px-2 text-meta text-muted hover:bg-surface hover:text-text">
        <X size={14} aria-hidden="true" />
        Dismiss
      </button>
    </li>
  )
}

/** Toasts: 6 s, dismissible, at most three stacked; announced politely. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const next = useRef(1)
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), [])
  const push = useCallback((t: ToastInput) => {
    const id = next.current++
    setItems((xs) => [...xs, { ...t, id }].slice(-MAX))
  }, [])
  const value = useMemo(() => push, [push])
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex justify-center px-4 pb-4 sm:justify-end">
        <ol aria-live="polite" aria-label="Notifications" className="flex w-full max-w-[420px] flex-col gap-2">
          {items.map((it) => (
            <Toast key={it.id} item={it} onDismiss={dismiss} />
          ))}
        </ol>
      </div>
    </Ctx.Provider>
  )
}
