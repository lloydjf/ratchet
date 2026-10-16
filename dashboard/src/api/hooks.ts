/** TanStack Query hooks, one per endpoint. Keys are stable so SSE can invalidate them. */
import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { setHostRunner } from '../copy'
import { api } from './select'
import type { CostsRange, RunsQuery, TracesQuery } from './client'
import type { Verdict, VerdictView } from './types'

export const qk = {
  meta: ['meta'] as const,
  scenarios: ['scenarios'] as const,
  verdicts: ['verdicts'] as const,
  verdict: (id: string) => ['verdict', id] as const,
  pair: (id: string, g: string, r: string) => ['pair', id, g, r] as const,
  edits: ['edits'] as const,
  edit: (id: string) => ['edit', id] as const,
  traces: (q: TracesQuery) => ['traces', q] as const,
  trace: (id: string) => ['trace', id] as const,
  runs: (q: RunsQuery) => ['runs', q] as const,
  costs: ['costs'] as const,
  costsIn: (r: CostsRange | null) => ['costs', r?.from ?? null, r?.to ?? null] as const,
  editLogs: (id: string) => ['edit-logs', id] as const,
  digest: ['digest'] as const,
  events: ['events'] as const,
  ticker: ['ticker'] as const,
  settings: ['settings'] as const,
}

export const useMeta = () =>
  useQuery({
    queryKey: qk.meta,
    queryFn: async () => {
      const meta = await api().getMeta()
      setHostRunner(meta.runner)
      return meta
    },
    staleTime: Infinity,
  })
export const useScenarios = () =>
  useQuery({ queryKey: qk.scenarios, queryFn: () => api().listScenarios(), staleTime: 5 * 60_000 })
export const useVerdicts = () => useQuery({ queryKey: qk.verdicts, queryFn: () => api().listVerdicts() })

export const isRunning = (v: VerdictView | undefined) => !!v && v.verdict == null

/** Orchestrator states after which Rollback / Approve are accepted (no 409 `not_ready`). */
export const TERMINAL_STATES: ReadonlySet<string> = new Set(['done', 'failed_infra'])
/** Decided but still acting/notifying: keep polling so the header settles on its own. */
export const isSettling = (v: VerdictView | undefined) => !!v && v.verdict != null && !TERMINAL_STATES.has(v.state)

export const useVerdict = (id: string | undefined) =>
  useQuery({
    queryKey: qk.verdict(id ?? ''),
    queryFn: () => api().getVerdict(id!),
    enabled: !!id,
    // Polling is the safety net under SSE: a stage must change on screen at least every 10 s.
    refetchInterval: (q) => (isRunning(q.state.data) || isSettling(q.state.data) ? 1500 : false),
  })

export const usePair = (id: string, g: string, r: string) =>
  useQuery({ queryKey: qk.pair(id, g, r), queryFn: () => api().getPair(id, g, r) })
export const useEdits = () => useQuery({ queryKey: qk.edits, queryFn: () => api().listEdits() })
export const useEdit = (id: string) => useQuery({ queryKey: qk.edit(id), queryFn: () => api().getEdit(id) })
// /traces and /settings answer 403 not_public on the public host: never call them there.
export const useTraces = (q: TracesQuery, enabled = true) =>
  useQuery({ queryKey: qk.traces(q), queryFn: () => api().listTraces(q), placeholderData: (prev) => prev, enabled })
/** Recent captured / live runs (operator host only), the Traces page "Recent runs" view. */
export const useRuns = (q: RunsQuery, enabled = true) =>
  useQuery({ queryKey: qk.runs(q), queryFn: () => api().listRuns(q), enabled })
export const useTrace = (id: string, enabled = true) =>
  useQuery({ queryKey: qk.trace(id), queryFn: () => api().getTrace(id), enabled })
export const useCosts = (range: CostsRange | null = null) =>
  useQuery({ queryKey: qk.costsIn(range), queryFn: () => api().getCosts(range), placeholderData: (prev) => prev })
/** Worker logs for the debug drawer; operator host only (never called on the public host). */
export const useEditLogs = (editId: string, enabled: boolean) =>
  useQuery({ queryKey: qk.editLogs(editId), queryFn: () => api().getEditLogs(editId), enabled, retry: false, staleTime: 10_000 })
export const useDigest = () => useQuery({ queryKey: qk.digest, queryFn: () => api().getDigest() })
export const useEvents = () =>
  useQuery({ queryKey: qk.events, queryFn: () => api().listEvents({ limit: 200 }), staleTime: 10_000 })
export const useSettings = (enabled = true) =>
  useQuery({ queryKey: qk.settings, queryFn: () => api().getSettings(), retry: false, enabled })
export const useTicker = () =>
  useQuery({
    queryKey: qk.ticker,
    queryFn: () => api().getTicker(),
    refetchInterval: (q) => (q.state.data?.live ? 1000 : 15_000),
  })

/** The `toast` on an action response (VerdictView / ReblessView), when the server sent one. */
export function responseToast(res: unknown): string | null {
  const t = res && typeof res === 'object' ? (res as { toast?: unknown }).toast : null
  return typeof t === 'string' && t.trim() ? t : null
}

function afterAction(qc: QueryClient, id: string, res: VerdictView | Verdict) {
  // The toast belongs to this one response; keep it out of the cached view.
  if ('verdict' in res && 'edit' in res) qc.setQueryData<VerdictView>(qk.verdict(id), { ...res, toast: null })
  else
    qc.setQueryData<VerdictView>(qk.verdict(id), (old) => (old ? { ...old, verdict: res as Verdict } : old))
  void qc.invalidateQueries({ queryKey: qk.verdict(id) })
  void qc.invalidateQueries({ queryKey: qk.verdicts })
  void qc.invalidateQueries({ queryKey: qk.events })
}

export function useRollback(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => api().rollback(id), onSuccess: (v) => afterAction(qc, id, v) })
}
export function useApprove(id: string) {
  const qc = useQueryClient()
  return useMutation({ mutationFn: () => api().approve(id), onSuccess: (v) => afterAction(qc, id, v) })
}
export function useRebless() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (p: { golden: string; replay: string }) => api().rebless(p.golden, p.replay),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['pair'] })
      void qc.invalidateQueries({ queryKey: ['traces'] })
      void qc.invalidateQueries({ queryKey: qk.events })
    },
  })
}
export function useSimulate() {
  return useMutation({ mutationFn: (scenarioId: string) => api().simulate(scenarioId) })
}
export function useUnbless(runId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api().unbless(runId),
    onSuccess: (t) => {
      qc.setQueryData(qk.trace(runId), t)
      void qc.invalidateQueries({ queryKey: ['traces'] })
      void qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}
/** `POST /runs/{id}/bless` for any run (the Recent runs list blesses row by row). */
export function useBlessRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => api().bless(runId),
    onSuccess: (t, runId) => {
      qc.setQueryData(qk.trace(runId), t)
      void qc.invalidateQueries({ queryKey: ['traces'] })
      void qc.invalidateQueries({ queryKey: ['runs'] })
    },
  })
}
