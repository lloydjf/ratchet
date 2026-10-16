/** Verdict page placeholder in the page's own shape: band, sieve field, pair rows. */
export function VerdictSkeleton() {
  return (
    <div className="stack" aria-busy="true" role="status" aria-label="Loading verdict">
      <div className="flex flex-col gap-3 panel-pad">
        <span className="skeleton block h-3.5 w-48" />
        <span className="skeleton block h-14 w-56" />
        <span className="skeleton block h-6 w-4/5" />
        <span className="skeleton block h-3.5 w-1/2" />
      </div>
      <div className="flex flex-col gap-3 panel-pad">
        <span className="skeleton block h-4 w-56" />
        <div className="grid h-[180px] grid-cols-[48px_repeat(4,1fr)] gap-6">
          {Array.from({ length: 5 }, (_, i) => (
            <div key={i} className="flex flex-col gap-2">
              <span className="skeleton block h-3 w-12" />
              <span className="skeleton block h-full w-[2px]" />
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-col">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="grid grid-cols-[6rem_1fr_6rem] items-center gap-4 border-t border-border px-6 py-3.5 first:border-t-0">
            <span className="skeleton h-5 w-20" />
            <span className="skeleton h-3.5 w-3/4" />
            <span className="skeleton h-3.5 w-20" />
          </div>
        ))}
      </div>
    </div>
  )
}
