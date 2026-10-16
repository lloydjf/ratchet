/** Static skeleton blocks (no shimmer: the only looping animation allowed is the stall pulse). */
export function SkeletonLine({ className = '' }: { className?: string }) {
  return <span aria-hidden="true" className={`skeleton block h-4 ${className}`} />
}

/** Skeleton list rows in the shape of a hairline list: a tag, a two-line title, a figure. */
export function SkeletonRows({ rows = 5, label = 'Loading' }: { rows?: number; label?: string }) {
  return (
    <div className="flex flex-col border border-border bg-surface" aria-busy="true" aria-label={label} role="status">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="grid grid-cols-[4.5rem_1fr_5rem] items-center gap-4 border-t border-border px-6 py-3.5 first:border-t-0 max-md:px-4">
          <span className="skeleton h-5 w-16" />
          <span className="flex flex-col gap-1.5">
            <span className="skeleton block h-3.5" style={{ width: `${82 - (i % 3) * 12}%` }} />
            <span className="skeleton block h-3 w-2/5" />
          </span>
          <span className="skeleton h-3.5 w-16 justify-self-end" />
        </div>
      ))}
    </div>
  )
}
