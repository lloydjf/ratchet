import { useSyncExternalStore } from 'react'

const QUERY = '(prefers-reduced-motion: reduce)'

function subscribe(cb: () => void) {
  try {
    const mq = window.matchMedia(QUERY)
    mq.addEventListener('change', cb)
    return () => mq.removeEventListener('change', cb)
  } catch {
    return () => {}
  }
}
function get(): boolean {
  try {
    return window.matchMedia(QUERY).matches
  } catch {
    return false
  }
}

export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, get, () => false)
}
