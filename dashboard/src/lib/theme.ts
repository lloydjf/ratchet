import { useCallback, useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'
const KEY = 'ratchet.theme'
const listeners = new Set<() => void>()

/** Dark by default: only a stored choice (the toggle) switches to light. */
export const DEFAULT_THEME: Theme = 'dark'

/** Run before the first render (module code, so a `self`-only CSP cannot block it). */
export function initTheme(): void {
  try {
    document.documentElement.dataset.theme = storedTheme() ?? DEFAULT_THEME
  } catch {
    /* no DOM */
  }
}

export function storedTheme(): Theme | null {
  try {
    const t = localStorage.getItem(KEY)
    return t === 'light' || t === 'dark' ? t : null
  } catch {
    return null
  }
}

export function currentTheme(): Theme {
  const attr = document.documentElement.dataset.theme
  if (attr === 'light' || attr === 'dark') return attr
  return storedTheme() ?? DEFAULT_THEME
}

export function setTheme(t: Theme): void {
  document.documentElement.dataset.theme = t
  try {
    localStorage.setItem(KEY, t)
  } catch {
    /* private mode: the choice lasts for this page only */
  }
  listeners.forEach((l) => l())
}

function subscribe(cb: () => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}

export function useTheme(): [Theme, () => void] {
  const theme = useSyncExternalStore(subscribe, currentTheme, () => 'dark' as Theme)
  const toggle = useCallback(() => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'), [])
  return [theme, toggle]
}
