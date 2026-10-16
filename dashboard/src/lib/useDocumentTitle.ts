import { useEffect } from 'react'
import { pageTitle } from '../copy'

/** Titles read `<Decision> — <skill> — Ratchet` in browser history. */
export function useDocumentTitle(parts: (string | null | undefined)[]): void {
  const title = pageTitle(parts)
  useEffect(() => {
    document.title = title
  }, [title])
}
