/**
 * Chooses the API implementation once, before the app renders.
 *
 * `VITE_FIXTURES=1` swaps in the in-memory fake API. The check is on a build-time
 * constant, so in a production build without the flag the dynamic import below is dead
 * code and the fixtures never reach the bundle.
 */
import { createHttpApi, type RatchetApi } from './client'

let current: RatchetApi | null = null

export const FIXTURES_ENABLED = import.meta.env.VITE_FIXTURES === '1'

export async function initApi(): Promise<RatchetApi> {
  if (current) return current
  if (import.meta.env.VITE_FIXTURES === '1') {
    const { createFixtureApi } = await import('./fixtures/index')
    current = createFixtureApi()
  } else {
    current = createHttpApi()
  }
  return current
}

export function api(): RatchetApi {
  if (!current) throw new Error('initApi() must run before api()')
  return current
}

/** Tests and the kit route inject their own implementation. */
export function setApi(next: RatchetApi): void {
  current = next
}
