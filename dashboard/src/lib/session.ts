/**
 * The Simulate session hash returned by POST /simulate (the cookie itself is HttpOnly).
 * Used only to decide whether this browser owns a verdict, since the public host lets a
 * judge act on their own runs only. sessionStorage, never a cookie (privacy).
 */
const KEY = 'ratchet.simulate.session'
let memory: string | null = null

export function rememberSession(hash: string | null | undefined): void {
  if (!hash) return
  memory = hash
  try {
    sessionStorage.setItem(KEY, hash)
  } catch {
    /* storage unavailable */
  }
}

export function mySession(): string | null {
  try {
    return sessionStorage.getItem(KEY) ?? memory
  } catch {
    return memory
  }
}
