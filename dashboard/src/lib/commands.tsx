import { createContext, useContext, useEffect, useRef } from 'react'

export interface CommandsCtx {
  openPalette: () => void
  openShortcuts: () => void
}

export const Commands = createContext<CommandsCtx>({ openPalette: () => {}, openShortcuts: () => {} })
export const useCommands = () => useContext(Commands)

/** True while focus is in a text field (single-key shortcuts must not fire there). */
export function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag === 'INPUT') {
    const type = (target as HTMLInputElement).type
    return !['checkbox', 'radio', 'button', 'submit', 'reset'].includes(type)
  }
  return false
}

/**
 * Single-key shortcuts for one page (j/k/o/r/a on the verdict page). Ignored while typing,
 * with a modifier held, or while a dialog is open.
 */
export function useHotkeys(map: Record<string, (e: KeyboardEvent) => void>, enabled = true): void {
  const ref = useRef(map)
  useEffect(() => {
    ref.current = map
  })
  useEffect(() => {
    if (!enabled) return
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented || isTyping(e.target)) return
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return
      const fn = ref.current[e.key]
      if (!fn) return
      e.preventDefault()
      fn(e)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [enabled])
}
