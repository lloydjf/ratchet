import { useEffect, useId, useState, type ReactNode } from 'react'

interface TooltipProps {
  content: ReactNode
  children: (props: { 'aria-describedby': string | undefined; onFocus: () => void; onBlur: () => void }) => ReactNode
  side?: 'top' | 'bottom'
  align?: 'center' | 'start' | 'end'
  className?: string
}

/**
 * Hover and keyboard tooltip (WCAG 1.4.13: dismissible with Escape, hoverable,
 * persistent while hovered or focused). The trigger must be focusable.
 */
export function Tooltip({ content, children, side = 'top', align = 'center', className = '' }: TooltipProps) {
  const [open, setOpen] = useState(false)
  const id = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  // The tooltip sits inside the hover wrapper, so moving onto it keeps it open.
  const show = () => setOpen(true)
  const hide = () => setOpen(false)

  const pos = side === 'top' ? 'bottom-full mb-2' : 'top-full mt-2'
  const al = align === 'center' ? 'left-1/2 -translate-x-1/2' : align === 'start' ? 'left-0' : 'right-0'

  return (
    <span className={`relative inline-flex ${className}`} onMouseEnter={show} onMouseLeave={hide}>
      {children({ 'aria-describedby': open ? id : undefined, onFocus: show, onBlur: hide })}
      <span
        role="tooltip"
        id={id}
        hidden={!open}
        className={`absolute ${pos} ${al} z-50 w-max max-w-[min(320px,calc(100vw-32px))] border border-border-strong bg-surface-2 px-3 py-2 text-left text-meta font-normal text-text normal-case tracking-normal whitespace-normal`}
      >
        {content}
      </span>
    </span>
  )
}
