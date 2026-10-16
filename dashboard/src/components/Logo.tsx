/** Ratchet wordmark: a sawtooth (the pawl only lets it turn one way). */
export function Logo({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true" className="shrink-0">
        <rect x="0.5" y="0.5" width="23" height="23" rx="6" fill="var(--surface-2)" stroke="var(--border)" />
        <path d="M4 17.5 L8 7 L9.6 17.5 L13.6 7 L15.2 17.5 L19.2 7 L20 17.5 Z" fill="var(--accent)" />
      </svg>
      <span className="text-body font-semibold tracking-[-0.01em] text-text">Ratchet</span>
    </span>
  )
}
