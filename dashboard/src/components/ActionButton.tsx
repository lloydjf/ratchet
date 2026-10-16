import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { BookmarkMinus, BookmarkPlus, GitMerge, GitPullRequestArrow, RotateCcw, RefreshCw } from 'lucide-react'
import { isApiError } from '../api/client'
import { ACTION_VERB, apiErrorCopy, CANCEL, CONFIRM_LABEL, confirmBody, confirmTitle, errorCopy, NOT_READY_RETRY_MS, undoHint as UNDO, WORKING, type ActionKind } from '../copy'
import { Button, type ButtonVariant } from './Button'
import { useToast } from './Toast'

interface ActionButtonProps {
  kind: ActionKind
  /** Commit sha (rollback), skill (approve, approveAnyway) or task (rebless, unbless, bless) used in the confirm body. */
  subject: string
  onConfirm: () => Promise<unknown>
  /** The exact git operation, shown in the confirm: [command, where]. */
  git?: string[] | null
  undoHint?: string
  disabled?: boolean
  doneLabel?: string
  done?: boolean
  size?: 'md' | 'sm'
  onClick?: () => void
  /** Hide the undo hint under the button (when a list shows it once). */
  hideHint?: boolean
  /** Where a failure is reported: inside the open confirm (default) or as a toast after collapsing it. */
  errorDisplay?: 'inline' | 'toast'
  /** Which edge the confirm grows from (the band's right edge: `end`). */
  align?: 'start' | 'end'
  /** Start expanded (the component kit). */
  defaultOpen?: boolean
}

const ICON = { rollback: RotateCcw, approve: GitMerge, approveAnyway: GitPullRequestArrow, rebless: RefreshCw, unbless: BookmarkMinus, bless: BookmarkPlus }
/** Approve anyway overrides a block, so it reads as a careful (danger) action, not a primary one. */
const TRIGGER: Record<ActionKind, ButtonVariant> = { rollback: 'danger', approve: 'primary', approveAnyway: 'danger', rebless: 'secondary', unbless: 'secondary', bless: 'primary' }
const CONFIRM_VARIANT: Record<ActionKind, ButtonVariant> = { rollback: 'danger', approve: 'primary', approveAnyway: 'danger', rebless: 'primary', unbless: 'danger', bless: 'primary' }

/**
 * Roll back / Approve / Re-bless / Bless / Unbless, confirmed in place: the button expands
 * into a two-step control that names the exact git action and the undo path, with Cancel
 * focused. Confirm runs it; the control collapses back into the result. Errors show inline
 * with code-mapped copy. A 409 `not_ready` keeps Confirm disabled for a few seconds.
 */
export function ActionButton({ kind, subject, onConfirm, git, undoHint, disabled, done, doneLabel, size = 'md', onClick, hideHint, errorDisplay = 'inline', align = 'start', defaultOpen = false }: ActionButtonProps) {
  const [open, setOpen] = useState(defaultOpen)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cooling, setCooling] = useState(false)
  const coolTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cancelRef = useRef<HTMLButtonElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const returnFocus = useRef(false)
  const id = useId()
  const toast = useToast()
  const Icon = ICON[kind]
  const body = confirmBody[kind](subject)

  useEffect(
    () => () => {
      if (coolTimer.current) clearTimeout(coolTimer.current)
    },
    [],
  )
  const first = useRef(true)
  useEffect(() => {
    const initial = first.current
    first.current = false
    if (open && !(initial && defaultOpen)) cancelRef.current?.focus()
    else if (returnFocus.current) {
      returnFocus.current = false
      triggerRef.current?.focus()
    }
  }, [open, defaultOpen])

  const collapse = () => {
    if (busy) return
    returnFocus.current = true
    setOpen(false)
    setError(null)
  }

  const confirm = async () => {
    setBusy(true)
    setError(null)
    try {
      await onConfirm()
      setOpen(false)
    } catch (e) {
      const code = isApiError(e) ? e.code : 'network'
      const text = apiErrorCopy(isApiError(e) ? e : null)
      if (code === 'not_ready') {
        setCooling(true)
        if (coolTimer.current) clearTimeout(coolTimer.current)
        coolTimer.current = setTimeout(() => setCooling(false), NOT_READY_RETRY_MS)
      }
      if (code === 'not_public') {
        // Owner-only on the public host: say so and get out of the way.
        setOpen(false)
        toast({ message: errorCopy('not_public'), tone: 'warn' })
        return
      }
      if (errorDisplay === 'toast') {
        setOpen(false)
        toast({ message: text, tone: 'warn' })
        return
      }
      setError(text)
    } finally {
      setBusy(false)
    }
  }

  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      collapse()
    }
  }

  if (!open)
    return (
      <div className={`flex flex-col gap-1 ${align === 'end' ? 'items-end' : 'items-start'}`}>
        <Button
          ref={triggerRef}
          variant={TRIGGER[kind]}
          size={size}
          disabled={disabled || done || cooling}
          onClick={() => {
            onClick?.()
            setOpen(true)
          }}
          aria-expanded={false}
          data-action={kind}
        >
          <Icon size={15} aria-hidden="true" />
          {done ? (doneLabel ?? ACTION_VERB[kind]) : ACTION_VERB[kind]}
        </Button>
        {hideHint ? null : <span className="text-meta text-muted">{undoHint ?? UNDO[kind]}</span>}
      </div>
    )

  return (
    <div
      role="group"
      aria-labelledby={`${id}-t`}
      aria-describedby={`${id}-b`}
      className="anim-confirm flex w-full max-w-[380px] flex-col gap-3 border border-border-strong bg-surface p-4"
      onKeyDown={onKey}
      data-testid="inline-confirm"
      data-kind={kind}
    >
      <p id={`${id}-t`} className="text-body font-semibold text-text">
        {confirmTitle[kind]}
      </p>
      <p id={`${id}-b`} className="text-meta text-muted">
        {body}
      </p>
      {git?.length ? (
        <p className="flex flex-wrap items-baseline gap-x-2 border border-border bg-bg px-3 py-2" data-testid="git-action">
          <code className="font-mono text-mono break-all text-text">{git[0]}</code>
          {git[1] ? <span className="text-meta text-muted">{git[1]}</span> : null}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="border tint-warn px-3 py-2 text-meta">
          {error}
        </p>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        <Button ref={cancelRef} variant="secondary" size="sm" onClick={collapse} disabled={busy}>
          {CANCEL}
        </Button>
        <Button variant={CONFIRM_VARIANT[kind]} size="sm" onClick={() => void confirm()} disabled={busy || cooling} data-testid="confirm-action">
          {busy ? WORKING : CONFIRM_LABEL[kind]}
        </Button>
      </div>
    </div>
  )
}
