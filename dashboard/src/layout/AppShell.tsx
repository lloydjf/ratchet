import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { Activity, BookMarked, Coins, Gavel, Menu, Moon, Newspaper, Play, Search, Sun, X, type LucideIcon } from 'lucide-react'
import { useCosts, useMeta, useTicker } from '../api/hooks'
import { useLiveLimited, useLiveStatus } from '../api/live'
import { track } from '../analytics'
import { BADGE, LIVE_LIMITED, LIVE_LIMITED_HINT, PRIVACY_NOTE, SHELL, TAGLINE } from '../copy'
import { Badge } from '../components/Badge'
import { CommandPalette, ShortcutSheet } from '../components/CommandPalette'
import { CostTicker } from '../components/CostTicker'
import { Logo } from '../components/Logo'
import { Commands, isTyping, type CommandsCtx } from '../lib/commands'
import { useTheme } from '../lib/theme'
import { ScrollRestorer } from './ScrollRestorer'

interface NavItem {
  to: string
  label: string
  icon: LucideIcon
  end?: boolean
}

export function navItems(publicMode: boolean): NavItem[] {
  return publicMode
    ? [
        { to: '/', label: 'Simulate', icon: Play, end: true },
        { to: '/verdicts', label: 'Verdicts', icon: Gavel },
        { to: '/timeline', label: 'Timeline', icon: Activity },
        { to: '/digest', label: 'Digest', icon: Newspaper },
        { to: '/costs', label: 'Costs', icon: Coins },
      ]
    : [
        { to: '/verdicts', label: 'Verdicts', icon: Gavel },
        { to: '/timeline', label: 'Timeline', icon: Activity },
        { to: '/digest', label: 'Digest', icon: Newspaper },
        { to: '/costs', label: 'Costs', icon: Coins },
        { to: '/traces', label: 'Traces', icon: BookMarked },
      ]
}

const isMac = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent)

/** The all-Ultra counterfactual at this week's cascade ratio, for the ticker's hover bar. */
function useCounterfactualToday(totalToday: number): number | null {
  const { data } = useCosts()
  const cf = data?.counterfactual
  if (!cf || cf.actual_usd <= 0 || totalToday <= 0) return null
  return totalToday * (cf.all_ultra_usd / cf.actual_usd)
}

export function HeaderTicker({ className = '', variant = 'rail' }: { className?: string; variant?: 'rail' | 'compact' }) {
  const { data } = useTicker()
  const cf = useCounterfactualToday(data?.total_usd ?? 0)
  if (!data)
    return (
      <div className={`flex flex-col gap-2 ${className}`} aria-hidden="true">
        <span className="skeleton block h-4 w-20" />
        <span className="skeleton block h-5 w-24" />
        <span className="skeleton block h-12 w-full" />
      </div>
    )
  return <CostTicker usdByTier={data.usd_by_tier} live={data.live} counterfactualUsd={cf} variant={variant} className={className} />
}

function ThemeToggle({ compact = false }: { compact?: boolean }) {
  const [theme, toggle] = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  const label = next === 'light' ? 'Light theme' : 'Dark theme'
  return (
    <button
      type="button"
      onClick={toggle}
      className={`inline-flex min-h-9 items-center gap-2.5 rounded-sm px-2 text-body text-muted hover:bg-surface-2 hover:text-text ${compact ? 'justify-center' : 'w-full'}`}
      aria-label={`Switch to ${next} theme`}
      title={compact ? label : undefined}
    >
      {theme === 'dark' ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
      {compact ? null : label}
    </button>
  )
}

/** Shown only while the host's SSE stream cap (429) has the feed polling instead. */
function LiveLimitedNote() {
  const limited = useLiveLimited()
  if (!limited) return null
  return (
    <p role="status" className="border-b border-border bg-surface px-4 py-2 text-meta text-muted" title={LIVE_LIMITED_HINT} data-testid="live-limited">
      {LIVE_LIMITED}
    </p>
  )
}

function LiveDot() {
  const status = useLiveStatus()
  const limited = useLiveLimited()
  const label = limited ? LIVE_LIMITED : status === 'open' ? 'Live' : status === 'polling' ? 'Live (polling)' : status === 'paused' ? 'Paused' : 'Connecting'
  return (
    <span className="sr-only" data-testid="live-status" data-status={status} data-limited={limited || undefined}>
      Event feed: {label}
    </span>
  )
}

function SearchButton({ onClick, compact = false }: { onClick: () => void; compact?: boolean }) {
  const mac = isMac()
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group flex min-h-9 items-center gap-2.5 rounded-sm border border-border bg-bg px-2.5 text-body text-muted hover:border-border-strong hover:text-text ${compact ? 'justify-center' : 'w-full'}`}
      aria-label={compact ? SHELL.search : undefined}
      aria-keyshortcuts={mac ? 'Meta+K' : 'Control+K'}
      title={compact ? SHELL.search : undefined}
      data-testid="open-palette"
    >
      <Search size={15} aria-hidden="true" className="shrink-0" />
      {compact ? null : (
        <>
          <span className="flex-1 truncate text-left">{SHELL.search}</span>
          <span className="kbd" aria-hidden="true">
            {mac ? '⌘K' : 'Ctrl K'}
          </span>
        </>
      )}
    </button>
  )
}

function NavList({ items, compact = false, onNavigate }: { items: NavItem[]; compact?: boolean; onNavigate?: () => void }) {
  return (
    <ul className="flex flex-col gap-0.5">
      {items.map((it) => {
        const Icon = it.icon
        return (
          <li key={it.to}>
            <NavLink
              to={it.to}
              end={it.end}
              onClick={onNavigate}
              title={compact ? it.label : undefined}
              aria-label={compact ? it.label : undefined}
              className={({ isActive }) =>
                `flex min-h-9 items-center gap-2.5 rounded-sm px-2 text-body font-medium transition-colors duration-[160ms] ${compact ? 'justify-center' : ''} ${isActive ? 'bg-surface-2 text-text' : 'text-muted hover:bg-surface-2 hover:text-text'}`
              }
            >
              {({ isActive }) => (
                <>
                  <Icon size={16} aria-hidden="true" className={isActive ? 'text-accent' : ''} />
                  {compact ? null : it.label}
                </>
              )}
            </NavLink>
          </li>
        )
      })}
    </ul>
  )
}

/** Rail body, shared by the persistent rail and the mobile sheet. */
function RailBody({ items, compact, onNavigate, openPalette, simulated }: { items: NavItem[]; compact: boolean; onNavigate?: () => void; openPalette: () => void; simulated: boolean }) {
  return (
    <>
      <div className={`flex h-14 shrink-0 items-center ${compact ? 'justify-center' : 'px-4'}`}>
        <Link to="/" onClick={onNavigate} className="flex min-h-9 items-center rounded-sm" aria-label="Ratchet home">
          <Logo collapsed={compact} />
        </Link>
      </div>
      <div className={compact ? 'px-2' : 'px-3'}>
        <SearchButton onClick={openPalette} compact={compact} />
      </div>
      <nav aria-label="Primary" className={`mt-4 ${compact ? 'px-2' : 'px-3'}`}>
        <NavList items={items} compact={compact} onNavigate={onNavigate} />
      </nav>
      <div className="flex-1" />
      <div className={`flex flex-col gap-4 border-t border-border py-4 ${compact ? 'items-center px-2' : 'px-4'}`}>
        {simulated ? compact ? <span className="font-mono text-meta text-warn-ink" title={BADGE.simulatedHint} aria-label={BADGE.simulated}>SIM</span> : <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} className="self-start" /> : null}
        <HeaderTicker variant={compact ? 'compact' : 'rail'} />
        <div className={compact ? '' : '-mx-2'}>
          <ThemeToggle compact={compact} />
        </div>
      </div>
    </>
  )
}

function MobileSheet({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const prev = document.activeElement as HTMLElement | null
    ref.current?.querySelector<HTMLElement>('a,button')?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key !== 'Tab' || !ref.current) return
      const els = Array.from(ref.current.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),[tabindex="0"]'))
      const first = els[0]
      const last = els.at(-1)
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last?.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      prev?.focus?.()
    }
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="fixed inset-0 z-[65] md:hidden">
      <div className="absolute inset-0 bg-bg/80" aria-hidden="true" onClick={onClose} />
      <div ref={ref} role="dialog" aria-modal="true" aria-label="Navigation" className="anim-sheet absolute inset-y-0 left-0 flex w-[280px] max-w-[85vw] flex-col border-r border-border bg-surface" data-testid="nav-sheet">
        <button type="button" onClick={onClose} className="absolute top-3 right-3 inline-flex h-9 w-9 items-center justify-center rounded-sm text-muted hover:bg-surface-2 hover:text-text" aria-label={SHELL.closeMenu}>
          <X size={18} aria-hidden="true" />
        </button>
        {children}
      </div>
    </div>
  )
}

function FooterLink({ href, children, onClick }: { href: string | null | undefined; children: ReactNode; onClick?: () => void }) {
  if (!href) return null
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" onClick={onClick} className="inline-flex min-h-8 items-center text-meta text-muted hover:text-text hover:underline">
      {children}
    </a>
  )
}

export function AppShell() {
  const { data: meta } = useMeta()
  const publicMode = meta?.public_mode ?? true
  const items = useMemo(() => navItems(publicMode), [publicMode])
  const [palette, setPalette] = useState(false)
  const [keys, setKeys] = useState(false)
  const [sheet, setSheet] = useState(false)
  const location = useLocation()
  const simulated = !!meta?.simulated_models

  const [seenPath, setSeenPath] = useState(location.pathname)
  if (seenPath !== location.pathname) {
    setSeenPath(location.pathname)
    if (sheet) setSheet(false)
  }

  const openPalette = useCallback(() => {
    setSheet(false)
    setPalette(true)
  }, [])
  const closePalette = useCallback(() => setPalette(false), [])
  const openShortcuts = useCallback(() => setKeys(true), [])
  const closeShortcuts = useCallback(() => setKeys(false), [])
  const commands = useMemo<CommandsCtx>(() => ({ openPalette, openShortcuts }), [openPalette, openShortcuts])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setPalette((p) => !p)
        return
      }
      if (e.key === '?' && !isTyping(e.target) && !document.querySelector('[role="dialog"][aria-modal="true"]')) {
        e.preventDefault()
        setKeys(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <Commands.Provider value={commands}>
      <div className="relative min-h-dvh bg-bg md:pl-14 rail:pl-[220px]">
        <div aria-hidden="true" className="absolute inset-y-0 left-0 hidden w-14 border-r border-border bg-surface md:block rail:w-[220px]" />
        <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[80] focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2">
          Skip to content
        </a>
        <ScrollRestorer />
        <LiveDot />

        <aside className="fixed inset-y-0 left-0 z-40 hidden w-14 flex-col overflow-y-auto border-r border-border bg-surface md:flex rail:w-[220px]" aria-label="Ratchet" data-testid="rail">
          <div className="hidden h-full flex-col rail:flex">
            <RailBody items={items} compact={false} openPalette={openPalette} simulated={simulated} />
          </div>
          <div className="flex h-full flex-col rail:hidden">
            <RailBody items={items} compact openPalette={openPalette} simulated={simulated} />
          </div>
        </aside>

        <header className="sticky top-0 z-40 flex h-12 items-center gap-2 border-b border-border bg-surface px-2 md:hidden">
          <button type="button" onClick={() => setSheet(true)} className="inline-flex h-10 w-10 items-center justify-center rounded-sm text-muted hover:bg-surface-2 hover:text-text" aria-label={SHELL.menu} aria-expanded={sheet} data-testid="open-sheet">
            <Menu size={18} aria-hidden="true" />
          </button>
          <Link to="/" className="flex min-h-10 items-center rounded-sm px-1" aria-label="Ratchet home">
            <Logo />
          </Link>
          <span className="flex-1" />
          {simulated ? <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} /> : null}
          <button type="button" onClick={openPalette} className="inline-flex h-10 w-10 items-center justify-center rounded-sm text-muted hover:bg-surface-2 hover:text-text" aria-label={SHELL.search}>
            <Search size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="flex min-h-dvh min-w-0 flex-col">
          <LiveLimitedNote />
          <main id="main" className="mx-auto w-full max-w-content flex-1 px-4 pt-4 pb-12 md:px-8 md:pt-8">
            <Suspense fallback={<PageSkeleton />}>
              <Outlet />
            </Suspense>
          </main>
          <footer className="mx-auto w-full max-w-content px-4 pb-8 md:px-8">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-1 border-t border-border pt-4">
              <span className="text-meta text-muted">
                Ratchet {meta?.version ? <span className="font-mono">v{meta.version}</span> : null} · {TAGLINE}
              </span>
              <FooterLink href={meta?.repo_url} onClick={() => track('demo.repo_click')}>
                Repository
              </FooterLink>
              <FooterLink href={meta?.video_url} onClick={() => track('demo.video_click')}>
                Video
              </FooterLink>
              <FooterLink href={meta?.repo_url ? `${meta.repo_url}#readme` : null} onClick={() => track('demo.repo_click', { target: 'readme' })}>
                README
              </FooterLink>
              <FooterLink href={meta?.bench_report_url}>Bench report</FooterLink>
              {!publicMode ? (
                <Link to="/settings" className="inline-flex min-h-8 items-center text-meta text-muted hover:text-text hover:underline">
                  Settings
                </Link>
              ) : null}
            </div>
            {publicMode ? <p className="mt-2 max-w-prose text-meta text-muted">{PRIVACY_NOTE}</p> : null}
          </footer>
        </div>

        <MobileSheet open={sheet} onClose={() => setSheet(false)}>
          <RailBody items={items} compact={false} onNavigate={() => setSheet(false)} openPalette={openPalette} simulated={simulated} />
        </MobileSheet>
        <CommandPalette open={palette} onClose={closePalette} onShortcuts={openShortcuts} />
        <ShortcutSheet open={keys} onClose={closeShortcuts} />
      </div>
    </Commands.Provider>
  )
}

export function PageSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading" role="status" className="stack">
      <div className="flex flex-col gap-3 panel-pad">
        <span className="skeleton block h-8 w-2/5" />
        <span className="skeleton block h-4 w-3/5" />
      </div>
      <div className="flex flex-col gap-2 panel-pad">
        {Array.from({ length: 4 }, (_, i) => (
          <span key={i} className="skeleton block h-9 w-full" />
        ))}
      </div>
    </div>
  )
}
