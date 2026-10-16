import { Suspense, type ReactNode } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { Moon, Sun } from 'lucide-react'
import { useMeta, useTicker } from '../api/hooks'
import { useLiveLimited, useLiveStatus } from '../api/live'
import { track } from '../analytics'
import { BADGE, LIVE_LIMITED, LIVE_LIMITED_HINT, PRIVACY_NOTE, TAGLINE } from '../copy'
import { Badge } from '../components/Badge'
import { CostTicker } from '../components/CostTicker'
import { useTheme } from '../lib/theme'
import { ScrollRestorer } from './ScrollRestorer'

/** Ratchet mark: a sawtooth wheel held by a pawl (it only turns one way). Source: docs/assets/src/make_logo.py. */
function RatchetMark() {
  return (
    <svg width="22" height="22" viewBox="0 0 64 64" fill="currentColor" aria-hidden="true" className="shrink-0 text-accent">
      <mask id="ratchet-mark-cut">
        <rect width="64" height="64" fill="#fff" />
        <path d="M37.9 19.5 39.3 13.9 15.8 4.8 12 14ZM16.9 8.1a6.1 6.1 0 1 0 -12.2 0a6.1 6.1 0 1 0 12.2 0Z" stroke="#000" strokeWidth="4.2" strokeLinejoin="round" />
      </mask>
      <path
        mask="url(#ratchet-mark-cut)"
        fillRule="evenodd"
        d="M38.6 20.3 40 13.8 43.3 14.7 49.8 28 55.4 24.4 57 27.3 52.3 41.3 58.8 42.7 57.8 46 44.6 52.5 48.2 58.1 45.2 59.7 31.2 55 29.9 61.5 26.6 60.5 20 47.3 14.5 50.9 12.8 47.9 17.5 33.9 11.1 32.6 12 29.3 25.2 22.7 21.6 17.2 24.6 15.5ZM42 37.6a7.1 7.1 0 1 0 -14.2 0a7.1 7.1 0 1 0 14.2 0Z"
      />
      <path d="M37.9 19.5 39.3 13.9 15.8 4.8 12 14Z" />
      <path fillRule="evenodd" d="M16.9 8.1a6.1 6.1 0 1 0 -12.2 0a6.1 6.1 0 1 0 12.2 0ZM13.2 8.1a2.4 2.4 0 1 0 -4.9 0a2.4 2.4 0 1 0 4.9 0Z" />
    </svg>
  )
}

function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2">
      <RatchetMark />
      <span className="text-body font-semibold tracking-[-0.01em] text-text">Ratchet</span>
    </span>
  )
}

interface NavItem {
  to: string
  label: string
  end?: boolean
}

export function navItems(publicMode: boolean): NavItem[] {
  return publicMode
    ? [
        { to: '/', label: 'Simulate', end: true },
        { to: '/timeline', label: 'Timeline' },
        { to: '/verdicts', label: 'Verdicts' },
        { to: '/digest', label: 'Digest' },
        { to: '/costs', label: 'Costs' },
      ]
    : [
        { to: '/timeline', label: 'Timeline' },
        { to: '/verdicts', label: 'Verdicts' },
        { to: '/traces', label: 'Traces' },
        { to: '/digest', label: 'Digest' },
        { to: '/costs', label: 'Costs' },
      ]
}

export function HeaderTicker({ className = '' }: { className?: string }) {
  const { data } = useTicker()
  if (!data) return <span className={`skeleton block h-10 w-36 ${className}`} aria-hidden="true" />
  return <CostTicker usdByTier={data.usd_by_tier} live={data.live} className={className} />
}

function ThemeToggle() {
  const [theme, toggle] = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button type="button" onClick={toggle} className="inline-flex min-h-11 items-center gap-2 rounded-sm px-2 text-meta text-muted hover:bg-surface hover:text-text" aria-label={`Switch to ${next} theme`}>
      {theme === 'dark' ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
      {next === 'light' ? 'Light theme' : 'Dark theme'}
    </button>
  )
}

function FooterLink({ href, children, onClick }: { href: string | null | undefined; children: ReactNode; onClick?: () => void }) {
  if (!href) return null
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" onClick={onClick} className="inline-flex min-h-11 items-center text-meta text-muted hover:text-text">
      {children}
    </a>
  )
}

/** Shown only while the host's SSE stream cap (429) has the feed polling instead. */
function LiveLimitedNote() {
  const limited = useLiveLimited()
  if (!limited) return null
  return (
    <p role="status" className="border-t border-border bg-surface px-4 py-1.5 text-center text-meta text-muted" title={LIVE_LIMITED_HINT} data-testid="live-limited">
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

export function AppShell() {
  const { data: meta } = useMeta()
  const publicMode = meta?.public_mode ?? true
  const items = navItems(publicMode)

  return (
    <div className="flex min-h-dvh flex-col bg-bg">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[80] focus:rounded-sm focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <ScrollRestorer />
      <LiveDot />
      <header className="sticky top-0 z-40 border-b border-border bg-bg/95 backdrop-blur-[6px]">
        <div className="mx-auto flex h-14 max-w-content items-center gap-6 px-4 sm:px-6">
          <Link to="/" className="flex min-h-11 items-center rounded-sm" aria-label="Ratchet home">
            <Wordmark />
          </Link>
          <nav aria-label="Primary" className="hidden md:block">
            <ul className="flex items-center gap-1">
              {items.map((it) => (
                <li key={it.to}>
                  <NavLink
                    to={it.to}
                    end={it.end}
                    className={({ isActive }) =>
                      `relative inline-flex min-h-11 items-center rounded-sm px-3 text-meta font-medium transition-colors duration-[160ms] ${isActive ? 'text-text after:absolute after:inset-x-3 after:-bottom-[7px] after:h-[2px] after:rounded-full after:bg-accent' : 'text-muted hover:text-text'}`
                    }
                  >
                    {it.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            {meta?.simulated_models ? <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} /> : null}
            <div className="hidden sm:block">
              <HeaderTicker />
            </div>
          </div>
        </div>
        <nav aria-label="Primary" className="border-t border-border md:hidden">
          <ul className="mx-auto grid max-w-content grid-cols-5 px-2">
            {items.map((it) => (
              <li key={it.to} className="min-w-0">
                <NavLink
                  to={it.to}
                  end={it.end}
                  className={({ isActive }) =>
                    `relative flex min-h-11 items-center justify-center truncate px-1 text-meta font-medium ${isActive ? 'text-text after:absolute after:inset-x-2 after:bottom-0 after:h-[2px] after:rounded-full after:bg-accent' : 'text-muted'}`
                  }
                >
                  {it.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <LiveLimitedNote />
      </header>

      <main id="main" className="mx-auto w-full max-w-content flex-1 px-4 pt-6 pb-16 sm:px-6 sm:pt-8">
        <Suspense fallback={<PageSkeleton />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-content flex-col gap-4 px-4 py-6 sm:px-6">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
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
              <Link to="/settings" className="inline-flex min-h-11 items-center text-meta text-muted hover:text-text">
                Settings
              </Link>
            ) : null}
            <span className="sm:ml-auto">
              <ThemeToggle />
            </span>
          </div>
          {publicMode ? <p className="max-w-prose text-meta text-muted">{PRIVACY_NOTE}</p> : null}
        </div>
      </footer>
    </div>
  )
}

export function PageSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading" role="status" className="flex flex-col gap-4">
      <span className="skeleton block h-9 w-2/3" />
      <span className="skeleton block h-5 w-1/3" />
      <span className="skeleton block h-40 w-full" />
    </div>
  )
}
