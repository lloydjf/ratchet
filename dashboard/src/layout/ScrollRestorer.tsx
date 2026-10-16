import { useEffect, useLayoutEffect } from 'react'
import { useLocation, useNavigationType } from 'react-router-dom'

const KEY = 'ratchet.scroll'

function read(): Record<string, number> {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? '{}') as Record<string, number>
  } catch {
    return {}
  }
}

/** Back navigation restores scroll; new navigations start at the top. */
export function ScrollRestorer() {
  const location = useLocation()
  const navType = useNavigationType()

  useEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual'
  }, [])

  useLayoutEffect(() => {
    const key = location.key
    if (navType === 'POP') {
      const y = read()[key]
      if (y != null) {
        // Content may still be loading; retry briefly until the page is tall enough.
        let tries = 0
        const attempt = () => {
          window.scrollTo(0, y)
          if (Math.abs(window.scrollY - y) > 2 && tries++ < 20) setTimeout(attempt, 50)
        }
        attempt()
      }
    } else if (!location.hash) {
      window.scrollTo(0, 0)
    }
    return () => {
      try {
        const all = read()
        all[key] = window.scrollY
        sessionStorage.setItem(KEY, JSON.stringify(all))
      } catch {
        /* storage unavailable */
      }
    }
  }, [location.key, location.hash, navType])

  return null
}
