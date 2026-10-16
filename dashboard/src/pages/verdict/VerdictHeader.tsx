import { useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { GitCommitHorizontal, RotateCcw } from 'lucide-react'
import { responseToast, useApprove, useRollback } from '../../api/hooks'
import { useLiveEvent, useLiveStatus } from '../../api/live'
import type { Event, Verdict, VerdictView } from '../../api/types'
import { track } from '../../analytics'
import {
  ACTED_COPY,
  BADGE,
  DECISION_ARIA,
  DECISION_DISPLAY,
  errorCopy,
  firstSentence,
  gitAction,
  HEADLINE_PREFIX,
  MAIN_UNCHANGED,
  OPEN_EDIT,
  REAPPLY_FROM_EDIT,
  replayInfra,
  rolledBackBy,
  stalledCopy,
  successToast,
  VERDICT,
} from '../../copy'
import { ActionButton } from '../../components/ActionButton'
import { Badge } from '../../components/Badge'
import { DECISION_TONE } from '../../components/DecisionChip'
import { useLocalClock, useStalled } from '../../components/ProgressStrip'
import { useToast } from '../../components/Toast'
import { UsdTiers } from '../../components/UsdTiers'
import { dateTime, duration, elapsed as fmtElapsed, shortSha, time } from '../../lib/format'
import { mySession } from '../../lib/session'

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="text-muted">{label}</dt>
      <dd className="text-text">{children}</dd>
    </div>
  )
}

/** Verdict-level reason codes that deserve their own copy in the band (say why, not just what). */
const HEADER_REASONS = ['replay_infra', 'judge_timeout', 'budget_exceeded', 'merge_conflict', 'stacked_on_held']

function reasonCode(v: Verdict): string | undefined {
  return v.reasons.find((r) => HEADER_REASONS.includes(r))
}

function ReasonNotice({ v, code }: { v: Verdict; code: string }) {
  const failed = v.pairs.filter((p) => p.outcome === 'failed').length
  const text = code === 'replay_infra' ? replayInfra(failed, v.pairs.length) : errorCopy(code)
  return (
    <p role="status" className="max-w-prose border tint-warn px-3 py-2 text-meta lg:col-start-1" data-testid="reason-notice" data-code={code}>
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
 * Actions follow the action Ratchet took: merged → Roll back; held → Approve and Roll back;
 * reverted (a BLOCK, or an edit someone rolled back) → Approve anyway only. That is the
 * override: the server re-applies the revert and merges. Roll back is hidden there (the
 * server would answer 409 `already_reverted`). Each confirms in place with its git action.
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
      <NotOwner className={className} />
    )

  const editLink = { label: OPEN_EDIT, to: `/edits/${encodeURIComponent(view.edit.edit_id)}` }
  const sha = shortSha(view.edit.commit)
  return (
    <div className={`flex shrink-0 flex-wrap items-start gap-3 lg:flex-nowrap lg:justify-end ${className}`} data-testid="verdict-actions" data-action-taken={v.action}>
      {v.action === 'held' ? (
        <ActionButton
          kind="approve"
          subject={skill}
          git={gitAction.approve()}
          align="end"
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
          git={gitAction.approveAnyway(shortSha(v.action_commit ?? view.edit.commit))}
          align="end"
          onClick={() => track('demo.action_click', { which: 'approve' })}
          onConfirm={async () => {
            const message = await serverToast('verdict.acted', () => approve.mutateAsync(), successToast.approveAnyway(skill))
            toast({ message, tone: 'pass' })
          }}
        />
      ) : (
        <ActionButton
          kind="rollback"
          subject={sha}
          git={gitAction.rollback(sha, v.action === 'merged')}
          align="end"
          onClick={() => track('demo.action_click', { which: 'rollback' })}
          onConfirm={async () => {
            const fallback = v.action === 'merged' ? successToast.rollbackMerged() : successToast.rollback()
            const message = await serverToast('verdict.rolled_back', () => rollback.mutateAsync(), fallback)
            toast({ message, tone: 'neutral', link: editLink })
          }}
        />
      )}
    </div>
  )
}

/** The owner-only sentence; its last "Simulate" goes to the landing, where a run is one click away. */
function NotOwner({ className }: { className: string }) {
  const text = errorCopy('not_owner')
  const at = text.lastIndexOf('Simulate')
  return (
    <p className={`max-w-[32ch] text-meta text-muted ${className}`} data-testid="not-owner">
      {at < 0 ? (
        text
      ) : (
        <>
          {text.slice(0, at)}
          <Link to="/" className="link">
            Simulate
          </Link>
          {text.slice(at + 'Simulate'.length)}
        </>
      )}
    </p>
  )
}

function StatusLine({ view }: { view: VerdictView }) {
  const v = view.verdict!
  const editHref = `/edits/${encodeURIComponent(view.edit.edit_id)}`
  if (isRolledBack(v))
    return (
      <p className="flex flex-wrap items-center gap-2 text-meta text-text lg:col-start-1" data-testid="rolled-back">
        <RotateCcw size={14} aria-hidden="true" className="text-muted" />
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
      <GitCommitHorizontal size={14} aria-hidden="true" />
      <span className="font-medium text-text">{ACTED_COPY[v.action]}</span>
      {v.action === 'reverted' ? (
        <span>
          {' · '}
          {MAIN_UNCHANGED}
        </span>
      ) : null}
      {v.action_commit ? (
        <span>
          {' · '}
          {VERDICT.commit} <span className="font-mono text-text">{shortSha(v.action_commit)}</span>
        </span>
      ) : null}
      {v.approved_at ? (
        <span>
          {' · '}
          {approvedWord} at {time(v.approved_at)}
        </span>
      ) : null}
    </p>
  )
}

/** "Blocked: the new …" shown as the reason sentence under the display word; the prefix stays for assistive tech. */
function Reason({ v, as: Tag }: { v: Verdict; as: 'h1' | 'h2' }) {
  const text = v.headline || firstSentence(v.narrative)
  const prefix = HEADLINE_PREFIX[v.decision]
  const rest = text.startsWith(prefix) ? text.slice(prefix.length).trimStart() : null
  // A sentence that opens with a skill name keeps its lowercase identifier.
  const keepCase = !!rest && v.affected_skills.some((s) => rest.startsWith(s))
  return (
    <Tag className="max-w-[62ch] text-h2 font-normal text-text lg:col-start-1" data-testid="verdict-headline">
      {rest ? (
        <>
          <span className="sr-only">{prefix} </span>
          <span className={`block ${keepCase ? '' : 'first-letter:uppercase'}`}>{rest}</span>
        </>
      ) : (
        text
      )}
    </Tag>
  )
}

interface BandProps {
  view: VerdictView
  /** The decision landed while this page was open: cross-fade from the stage line (320 ms). */
  animate: boolean
  simulatedMeta: boolean
  publicMode: boolean
  /** The landing hero: no actions, no page heading. */
  readOnly?: boolean
  testId?: string
}

/**
 * The decision band. While running it carries the current stage at 20 px; on
 * `verdict.decided` it cross-fades once to the display word ("Blocked." / "Held." /
 * "Passed.") with the reason beneath, a 6 % tint of the decision hue behind, and the
 * action at its right edge. DOM order: decision → reason → actions → metadata.
 */
export function VerdictBand({ view, animate, simulatedMeta, publicMode, readOnly = false, testId = 'verdict-header' }: BandProps) {
  const v = view.verdict
  const p = view.progress
  const stage = p?.stage_label ?? VERDICT.queued
  // The last stage shown while running, kept for the cross-fade into the decision.
  const [lastStage, setLastStage] = useState('')
  if (!v && lastStage !== stage) setLastStage(stage)
  const running = !v
  const shownElapsed = useLocalClock(p?.elapsed_s ?? 0, running && !!p)
  const stalled = useStalled(stage, running && !!p) || !!p?.stalled
  const skill = view.edit.affected_skills.join(', ')
  const Heading = readOnly ? 'h2' : 'h1'

  if (!v) {
    const done = p?.stages.filter((s) => s.status === 'done').length ?? 0
    const total = p?.stages.filter((s) => s.status !== 'skipped' && s.key !== 'done').length ?? 0
    return (
      <header className="relative bg-surface panel-pad transition-[background-color] duration-[320ms]" data-testid={testId} data-running="true">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Heading className="text-body font-medium text-muted">
              {VERDICT.checking} <span className="font-mono text-text">{skill}</span>
            </Heading>
            {simulatedMeta ? <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} /> : null}
          </div>
          <p className="text-h2 font-semibold text-text" aria-live="polite" aria-atomic="true" data-testid="band-stage" data-stage={p?.stage ?? 'queued'}>
            <span key={stage} className="anim-stage">
              {stage}
            </span>
          </p>
          <p className="num flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
            <span aria-label={`Elapsed ${Math.floor(shownElapsed)} seconds`}>{fmtElapsed(shownElapsed)}</span>
            {p ? <UsdTiers map={p.usd_by_tier} none="no model yet" /> : null}
            {total ? (
              <span>
                {done}/{total} stages
              </span>
            ) : null}
          </p>
          {stalled ? (
            <p className="text-meta text-warn-ink" data-testid="stalled-copy">
              {stalledCopy(stage)}
            </p>
          ) : (
            <p className="max-w-prose text-meta text-muted">{VERDICT.checkingBody}</p>
          )}
        </div>
      </header>
    )
  }

  const tone = DECISION_TONE[v.decision]
  const code = reasonCode(v)
  return (
    <header className={`relative panel-pad band-${tone} transition-[background-color] duration-[320ms]`} data-testid={testId} data-decision={v.decision} data-action={v.action}>
      {animate && lastStage ? (
        <p aria-hidden="true" className="anim-band-out pointer-events-none absolute top-6 left-6 text-h2 font-semibold text-text max-md:top-4 max-md:left-4">
          {lastStage}
        </p>
      ) : null}
      <div className={`grid grid-cols-1 gap-y-3 lg:grid-cols-[minmax(0,1fr)_auto] lg:grid-rows-[auto_auto_auto_auto_auto_1fr] lg:gap-x-10 ${animate ? 'anim-band-in' : ''}`}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-meta lg:col-start-1">
          <span className="font-mono text-text">{skill}</span>
          <span className="text-muted">
            {VERDICT.commit} <span className="font-mono">{shortSha(view.edit.commit)}</span>
          </span>
          {v.recorded ? <Badge text={BADGE.recorded} tone="neutral" hint={BADGE.recordedHint} /> : null}
          {v.simulated_models || simulatedMeta ? <Badge text={BADGE.simulated} tone="warn" hint={BADGE.simulatedHint} /> : null}
        </div>
        <p role="img" aria-label={DECISION_ARIA[v.decision]} data-decision={v.decision} data-testid="decision-word" className="text-display font-semibold tracking-[-0.02em] lg:col-start-1" style={{ color: `var(--${tone}-ink)` }}>
          <span aria-hidden="true">{DECISION_DISPLAY[v.decision]}</span>
        </p>
        <Reason v={v} as={Heading} />
        {readOnly ? null : <VerdictActions view={view} publicMode={publicMode} className="order-last mt-2 lg:order-none lg:col-start-2 lg:row-span-6 lg:row-start-1 lg:mt-0 lg:self-start" />}
        <dl className="num mt-1 flex flex-wrap gap-x-5 gap-y-1 text-meta lg:col-start-1">
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
