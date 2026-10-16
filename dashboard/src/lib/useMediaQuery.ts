import { useCallback, useSyncExternalStore } from 'react'

export function useMediaQuery(query: string, fallback = false): boolean {
  const subscribe = useCallback(
    (cb: () => void) => {
      try {
        const mq = window.matchMedia(query)
        mq.addEventListener('change', cb)
        return () => mq.removeEventListener('change', cb)
      } catch {
        return () => {}
      }
    },
    [query],
  )
  const get = () => {
    try {
      return window.matchMedia(query).matches
    } catch {
      return fallback
    }
  }
  return useSyncExternalStore(subscribe, get, () => fallback)
}

/** Below 768 px the product switches to single-column, card-based layouts. */
export const useIsMobile = () => !useMediaQuery('(min-width: 768px)', true)
