import { useRef, type CSSProperties, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { GitCommitHorizontal } from 'lucide-react'
import { responseToast, useApprove, useRollback } from '../../api/hooks'
import { useLiveEvent, useLiveStatus } from '../../api/live'
import type { Event, Verdict, VerdictView } from '../../api/types'
import { track } from '../../analytics'
import {
  ACTED_COPY,
  BADGE,
  errorCopy,
  firstSentence,
  MAIN_UNCHANGED,
  OPEN_EDIT,
  REAPPLY_FROM_EDIT,
  replayInfra,
  rolledBackBy,
  successToast,
  VERDICT,
} from '../../copy'
import { ActionButton } from '../../components/ActionButton'
import { Badge } from '../../components/Badge'
import { DecisionChip, DECISION_TONE } from '../../components/DecisionChip'
import { ProgressStrip } from '../../components/ProgressStrip'
import { useToast } from '../../components/Toast'
import { UsdTiers } from '../../components/UsdTiers'
import { dateTime, duration, shortSha, time } from '../../lib/format'
import { mySession } from '../../lib/session'

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-muted">{label}</dt>
      <dd className="text-text">{children}</dd>
    </div>
  )
}

/** Verdict-level reason codes that deserve their own copy in the header (say why, not just what). */
const HEADER_REASONS = ['replay_infra', 'judge_timeout', 'budget_exceeded', 'merge_conflict', 'stacked_on_held']

function reasonCode(v: Verdict): string | undefined {
  return v.reasons.find((r) => HEADER_REASONS.includes(r))
}

function ReasonNotice({ v, code }: { v: Verdict; code: string }) {
  const failed = v.pairs.filter((p) => p.outcome === 'failed').length
  const text = code === 'replay_infra' ? replayInfra(failed, v.pairs.length) : errorCopy(code)
  return (
    <p role="status" className="max-w-prose rounded-sm border tint-warn px-3 py-2 text-meta lg:col-start-1" data-testid="reason-notice" data-code={code}>
      {text}
    </p>
  )
}

/** Rolled back by a person (not a BLOCK that Ratchet reverted itself) and not re-approved since. */
export const isRolledBack = (v: Verdict) => v.action === 'reverted' && !!v.rolled_back_at

type ActionEventKind = 'verdict.acted' | 'verdict.rolled_back'

/**
 * The server's own toast line for an action. The action response carries it (`toast`) and
 * is shown at once. Older backends answer without it; then wait briefly for the `toast` on
 * the `verdict.acted` / `verdict.rolled_back` event (only while the stream is open; polling
 * would be too slow to wait for). The client template, worded the same, is the last fallback.
 */
export function useServerToast(verdictId: string) {
  const status = useLiveStatus()
  const seen = useRef(new Map<ActionEventKind, string>())
  const waiter = useRef<{ kind: ActionEventKind; resolve: (t: string) => void } | null>(null)
  useLiveEvent((e: Event) => {
    if (e.kind !== 'verdict.acted' && e.kind !== 'verdict.rolled_back') return
    const kind = e.kind as ActionEventKind
    if (e.payload.verdict_id !== verdictId && e.ref !== verdictId) return
    const toast = typeof e.payload.toast === 'string' && e.payload.toast ? e.payload.toast : null
    if (!toast) return
    seen.current.set(kind, toast)
    if (waiter.current?.kind === kind) waiter.current.resolve(toast)
  })
  return async (kind: ActionEventKind, run: () => Promise<unknown>, fallback: string, waitMs = 1200): Promise<string> => {
    seen.current.delete(kind)
    const direct = responseToast(await run())
    if (direct) return direct
    const now = seen.current.get(kind)
    if (now) return now
    if (status !== 'open') return fallback
    return new Promise<string>((resolve) => {
      const timer = setTimeout(() => {
        waiter.current = null
        resolve(fallback)
      }, waitMs)
      waiter.current = {
        kind,
        resolve: (t) => {
          clearTimeout(timer)
          waiter.current = null
          resolve(t)
        },
      }
    })
  }
}

/**
 * Actions follow the action Ratchet took: merged → Rollback; held → Approve and
 * Rollback; reverted (a BLOCK, or an edit someone rolled back) → Approve anyway only. That is
 * the override: the server re-applies the revert and merges. Rollback is hidden there (the
 * server would answer 409 `already_reverted`).
 */
export function VerdictActions({ view, publicMode, className = '' }: { view: VerdictView; publicMode: boolean; className?: string }) {
  const v = view.verdict!
  const toast = useToast()
  const rollback = useRollback(v.verdict_id)
  const approve = useApprove(v.verdict_id)
  const serverToast = useServerToast(v.verdict_id)
  const skill = v.affected_skills.join(', ') || 'the skill'
  // Public host: actions are owner-only (403 not_public otherwise).
  if (publicMode && (!v.session_hash || v.session_hash !== mySession()))
    return (
      <p className={`max-w-[28ch] text-meta text-muted ${className}`} data-testid="not-owner">
        {errorCopy('not_owner')}
      </p>
    )

  const editLink = { label: OPEN_EDIT, to: `/edits/${encodeURIComponent(view.edit.edit_id)}` }
  const doRollback = (
    <ActionButton
      kind="rollback"
      subject={shortSha(view.edit.commit)}
      onClick={() => track('demo.action_click', { which: 'rollback' })}
      onConfirm={async () => {
        const fallback = v.action === 'merged' ? successToast.rollbackMerged() : successToast.rollback()
        const message = await serverToast('verdict.rolled_back', () => rollback.mutateAsync(), fallback)
        toast({ message, tone: 'neutral', link: editLink })
      }}
    />
  )
  return (
    <div className={`flex shrink-0 flex-wrap items-start gap-3 lg:flex-nowrap ${className}`} data-testid="verdict-actions" data-action-taken={v.action}>
      {v.action === 'held' ? (
        <ActionButton
          kind="approve"
          subject={skill}
          onClick={() => track('demo.action_click', { which: 'approve' })}
          onConfirm={async () => {
            const message = await serverToast('verdict.acted', () => approve.mutateAsync(), successToast.approve(skill))
            toast({ message, tone: 'pass' })
          }}
        />
      ) : null}
      {v.action === 'reverted' ? (
        <ActionButton
          kind="approveAnyway"
          subject={skill}
          onClick={() => track('demo.action_click', { which: 'approve' })}
          onConfirm={async () => {
            const message = await serverToast('verdict.acted', () => approve.mutateAsync(), successToast.approveAnyway(skill))
            toast({ message, tone: 'pass' })
          }}
        />
      ) : (
        doRollback
      )}
    </div>
  )
}

function StatusLine({ view }: { view: VerdictView }) {
  const v = view.verdict!
  const editHref = `/edits/${encodeURIComponent(view.edit.edit_id)}`
  if (isRolledBack(v))
    return (
      <p className="flex flex-wrap items-center gap-2 text-meta text-text lg:col-start-1" data-testid="rolled-back">
        <span className="inline-flex h-6 items-center rounded-sm border px-2 font-mono text-mono font-semibold tint-neutral" aria-hidden="true">
          ↺
        </span>
        <span className="font-medium">{rolledBackBy(v.rolled_back_by ?? 'someone', time(v.rolled_back_at))}</span>
        {v.action_commit ? (
          <span className="text-muted">
            · {VERDICT.revertCommit} <span className="font-mono text-text">{shortSha(v.action_commit)}</span> ·{' '}
            <Link to={editHref} className="link">
              {REAPPLY_FROM_EDIT}
            </Link>
          </span>
        ) : null}
      </p>
    )
  // A merged BLOCK, or a merged edit that was rolled back before, was approved anyway.
  const approvedWord = v.decision === 'BLOCK' || v.rolled_back_at ? VERDICT.approvedAnyway : VERDICT.approved
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-meta text-muted lg:col-start-1" data-testid="acted-status" data-action={v.action}>
      <GitCommitHorizontal size={16} aria-hidden="true" />
      <span className="font-medium text-text">{ACTED_COPY[v.action]}</span>
      {v.action === 'reverted' ? <span>{' · '}{MAIN_UNCHANGED}</span> : null}
      {v.action_commit ? (
        <span>
          {' · '}{VERDICT.commit} <span className="font-mono text-text">{shortSha(v.action_commit)}</span>
        </span>
      ) : null}
      {v.approved_at ? <span>{' · '}{approvedWord} at {time(v.approved_at)}</span> : null}
    </p>
  )
}

/**
 * Decided header. DOM (and screen-reader) order is decision → title → actions → metadata
 * (reading order); on wide screens the actions still sit right-aligned beside the whole band.
 */
export function DecidedHeader({ view, animate, simulatedMeta, publicMode }: { view: VerdictView; animate: boolean; simulatedMeta: boolean; publicMode: boolean }) {
  const v = view.verdict!
  const tone = DECISION_TONE[v.decision]
  const code = reasonCode(v)
  // Left-column rows the actions span on wide screens: badges, title, metadata, [notice], status.
  const rows = code ? 5 : 4
  return (
    <header className="relative overflow-hidden rounded-card border border-border bg-surface" data-testid="verdict-header" data-decision={v.decision} data-action={v.action}>
      <span aria-hidden="true" className="absolute inset-x-0 top-0 h-[3px]" style={{ background: `var(--${tone})` }} />
      <div className="grid grid-cols-1 gap-y-4 p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_auto] lg:gap-x-8" style={{ '--rows': rows } as CSSProperties}>
        <div className="flex flex-wrap items-center gap-2 lg:col-start-1">
          <DecisionChip decision={v.decision} size="lg" animate={animate} />
          {v.recorded ? <Badge text={BADGE.recorded} tone="neutral" hint={BADGE.recordedHint} /> : null}
          {v.simulated_models || simulatedMeta ? <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} /> : null}
        </div>
        <h1 className="max-w-[46ch] text-h1 font-semibold text-text lg:col-start-1" data-testid="verdict-headline">
          {v.headline || firstSentence(v.narrative)}
        </h1>
        <VerdictActions view={view} publicMode={publicMode} className="order-last mt-1 lg:order-none lg:col-start-2 lg:row-start-1 lg:mt-0 lg:self-start lg:[grid-row-end:span_var(--rows)]" />
        <dl className="num flex flex-wrap gap-x-5 gap-y-1 text-meta lg:col-start-1">
          <Meta label={v.affected_skills.length > 1 ? VERDICT.skills : VERDICT.skill}>
            <span className="font-mono">{v.affected_skills.join(', ')}</span>
          </Meta>
          <Meta label={VERDICT.editBy}>{view.edit.author === 'seeded' ? VERDICT.seeded : view.edit.author}</Meta>
          <Meta label={VERDICT.decided}>{dateTime(v.created_at)}</Meta>
          <Meta label={VERDICT.took}>{duration(v.duration_s)}</Meta>
          <Meta label={VERDICT.judgesCost}>
            <UsdTiers map={v.cost} />
          </Meta>
        </dl>
        {code ? <ReasonNotice v={v} code={code} /> : null}
        <StatusLine view={view} />
      </div>
    </header>
  )
}

export function RunningHeader({ view }: { view: VerdictView }) {
  const p = view.progress
  const skill = view.edit.affected_skills.join(', ')
  return (
    <header className="flex flex-col gap-3" data-testid="verdict-header" data-running="true">
      <div className="sticky top-[101px] z-30 -mx-4 bg-bg/95 px-4 py-2 backdrop-blur-[6px] sm:-mx-6 sm:px-6 md:top-14">
        {p ? (
          <ProgressStrip
            stage={p.stage}
            stageLabel={p.stage_label}
            k={p.k}
            n={p.n}
            elapsedS={p.elapsed_s}
            usdByTier={p.usd_by_tier}
            stages={p.stages}
            stalled={p.stalled}
          />
        ) : (
          <div className="rounded-card border border-border bg-surface p-4 text-body text-text" role="status">
            {VERDICT.queued}
          </div>
        )}
      </div>
      <h1 className="text-h1 font-semibold text-text">
        {VERDICT.checking} <span className="font-mono">{skill}</span>
      </h1>
      <p className="text-meta text-muted">{VERDICT.checkingBody}</p>
    </header>
  )
}
