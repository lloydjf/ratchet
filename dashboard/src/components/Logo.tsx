import { MARK_VIEWBOX, MARK_WHEEL_PATH, WORDMARK_PATH, WORDMARK_VIEWBOX } from './brand'

/** The mark: a ratchet wheel cut from a rounded square, in the ink of the current theme. */
export function LogoMark({ size = 24, className = '' }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox={MARK_VIEWBOX} aria-hidden="true" className={`shrink-0 ${className}`}>
      <rect x="-122" y="-122" width="244" height="244" rx="64" fill="var(--text)" />
      <path d={MARK_WHEEL_PATH} fill="var(--bg)" fillRule="evenodd" />
    </svg>
  )
}

/** The wordmark: the only lettering treatment for the name. */
export function Wordmark({ height = 14, className = '' }: { height?: number; className?: string }) {
  const width = (668 / 142) * height
  return (
    <svg width={width} height={height} viewBox={WORDMARK_VIEWBOX} aria-hidden="true" className={`shrink-0 ${className}`}>
      <path d={WORDMARK_PATH} fill="var(--text)" />
    </svg>
  )
}

/** Mark plus wordmark (the rail, the mobile bar). */
export function Logo({ collapsed = false, className = '' }: { collapsed?: boolean; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <LogoMark size={24} />
      {collapsed ? null : <Wordmark height={15} />}
      <span className="sr-only">Ratchet</span>
    </span>
  )
}
