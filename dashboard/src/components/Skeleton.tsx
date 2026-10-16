/** Static skeleton blocks (no shimmer: the only looping animation allowed is the stall pulse). */
export function SkeletonLine({ className = '' }: { className?: string }) {
  return <span aria-hidden="true" className={`skeleton block h-4 ${className}`} />
}

export function SkeletonRows({ rows = 5, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="flex flex-col gap-2" aria-busy="true" aria-label={label} role="status">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex h-14 items-center gap-4 rounded-sm border border-border bg-surface px-4">
          <span className="skeleton h-6 w-20" />
          <span className="skeleton h-4 flex-1" />
          <span className="skeleton h-4 w-16" />
        </div>
      ))}
    </div>
  )
}
