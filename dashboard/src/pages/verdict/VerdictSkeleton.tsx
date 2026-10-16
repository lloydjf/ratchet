/** Verdict page placeholder: the header band loads first. */
export function VerdictSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-busy="true" role="status" aria-label="Loading verdict">
      <div className="rounded-card border border-border bg-surface p-6">
        <span className="skeleton block h-11 w-32" />
        <span className="skeleton mt-4 block h-8 w-4/5" />
        <span className="skeleton mt-3 block h-5 w-1/2" />
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="flex flex-col gap-3 lg:col-span-2">
          <span className="skeleton block h-5 w-full" />
          <span className="skeleton block h-5 w-11/12" />
          <span className="skeleton block h-5 w-3/4" />
        </div>
        <span className="skeleton block h-48" />
      </div>
    </div>
  )
}
