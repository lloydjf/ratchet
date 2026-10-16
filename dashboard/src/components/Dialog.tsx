import { useEffect, useId, useRef, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'

interface DialogProps {
  open: boolean
  title: string
  children: ReactNode
  footer?: ReactNode
  onClose: () => void
  /** Element to focus first; defaults to the first focusable element. */
  initialFocus?: RefObject<HTMLElement | null>
  /** Title for assistive tech only (the command palette shows an input instead). */
  hideTitle?: boolean
  /** `top`: sits near the top of the viewport (the command palette). */
  position?: 'center' | 'top'
  width?: number
  testId?: string
}

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])'

/**
 * Overlay for tasks that need protected focus: the command palette and the shortcut
 * sheet. Focus trap, Escape to close, focus returns to where it was. Confirmations never
 * use it; they expand in place.
 */
export function Dialog({ open, title, children, footer, onClose, initialFocus, hideTitle = false, position = 'center', width = 480, testId = 'dialog' }: DialogProps) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const onCloseRef = useRef(onClose)
  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const node = ref.current
    const first = initialFocus?.current ?? node?.querySelector<HTMLElement>(FOCUSABLE)
    first?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onCloseRef.current()
        return
      }
      if (e.key !== 'Tab' || !node) return
      const els = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (!els.length) return
      const firstEl = els[0]!
      const lastEl = els[els.length - 1]!
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault()
        lastEl.focus()
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault()
        firstEl.focus()
      }
    }
    document.addEventListener('keydown', onKey, true)
    const prevOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey, true)
      document.body.style.overflow = prevOverflow
      previous?.focus?.()
    }
  }, [open, initialFocus])

  if (!open) return null
  return createPortal(
    <div className={`fixed inset-0 z-[70] flex justify-center p-4 ${position === 'top' ? 'items-start pt-[12vh]' : 'items-end sm:items-center'}`}>
      <div className="absolute inset-0 bg-bg/80" aria-hidden="true" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="anim-confirm relative w-full border border-border-strong bg-surface"
        style={{ maxWidth: width }}
        data-testid={testId}
      >
        <h2 id={titleId} className={hideTitle ? 'sr-only' : 'border-b border-border px-5 py-4 text-h3 font-semibold text-text'}>
          {title}
        </h2>
        <div className={hideTitle ? '' : 'px-5 py-4 text-body text-text'}>{children}</div>
        {footer ? <div className="flex flex-col-reverse gap-2 border-t border-border px-5 py-3 sm:flex-row sm:justify-end">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  )
}
