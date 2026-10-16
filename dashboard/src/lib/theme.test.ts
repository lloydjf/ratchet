import { afterEach, describe, expect, it } from 'vitest'
import { currentTheme, DEFAULT_THEME, initTheme, setTheme } from './theme'

afterEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('theme bootstrap', () => {
  it('renders dark when nothing is stored, whatever the system prefers', () => {
    window.matchMedia = ((q: string) => ({ matches: q.includes('light'), media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia
    initTheme()
    expect(DEFAULT_THEME).toBe('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(currentTheme()).toBe('dark')
  })

  it('the toggle switches and persists; the stored choice wins on the next load', () => {
    setTheme('light')
    expect(localStorage.getItem('ratchet.theme')).toBe('light')
    delete document.documentElement.dataset.theme
    initTheme()
    expect(document.documentElement.dataset.theme).toBe('light')
    setTheme('dark')
    initTheme()
    expect(currentTheme()).toBe('dark')
  })
})
