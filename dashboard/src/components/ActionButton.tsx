import { useEffect, useRef, useState } from 'react'
import { BookmarkMinus, BookmarkPlus, GitMerge, GitPullRequestArrow, RotateCcw, RefreshCw } from 'lucide-react'
import { isApiError } from '../api/client'
import { ACTION_LABEL, apiErrorCopy, CANCEL, CONFIRM_LABEL, confirmBody, confirmTitle, errorCopy, NOT_READY_RETRY_MS, undoHint as UNDO, WORKING, type ActionKind } from '../copy'
import { Button, type ButtonVariant } from './Button'
import { Dialog } from './Dialog'
import { useToast } from './Toast'

interface ActionButtonProps {
  kind: ActionKind
  /** Commit sha (rollback), skill (approve, approveAnyway) or task (rebless, unbless, bless) used in the confirm body. */
  subject: string
  onConfirm: () => Promise<unknown>
  undoHint?: string
  disabled?: boolean
  doneLabel?: string
  done?: boolean
  size?: 'md' | 'sm'
  onClick?: () => void
  /** Hide the undo hint under the button (when a list shows it once). */
  hideHint?: boolean
  /** Where a failure is reported: inside the open dialog (default) or as a toast after closing it. */
  errorDisplay?: 'inline' | 'toast'
}

const ICON = { rollback: RotateCcw, approve: GitMerge, approveAnyway: GitPullRequestArrow, rebless: RefreshCw, unbless: BookmarkMinus, bless: BookmarkPlus }
/** Trigger styling. Approve anyway overrides a block, so it reads as a careful (danger) action, not a primary one. */
const TRIGGER: Record<ActionKind, ButtonVariant> = { rollback: 'danger', approve: 'primary', approveAnyway: 'danger', rebless: 'secondary', unbless: 'secondary', bless: 'primary' }
const CONFIRM_VARIANT: Record<ActionKind, ButtonVariant> = { rollback: 'danger', approve: 'primary', approveAnyway: 'danger', rebless: 'primary', unbless: 'danger', bless: 'primary' }

/**
 * Rollback / Approve / Re-bless / Bless / Unbless. Always confirms with the exact git action and the undo
 * path, then reports errors inline with code-mapped copy. A 409 `not_ready` (the
 * edit is still being acted on) keeps the action disabled for a few seconds, then lets the
 * user try again.
 */
export function ActionButton({ kind, subject, onConfirm, undoHint, disabled, done, doneLabel, size = 'md', onClick, hideHint, errorDisplay = 'inline' }: ActionButtonProps) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [cooling, setCooling] = useState(false)
  const coolTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (coolTimer.current) clearTimeout(coolTimer.current)
  }, [])
  const cancelRef = useRef<HTMLButtonElement>(null)
  const toast = useToast()
  const Icon = ICON[kind]
  const body = confirmBody[kind](subject)

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

  return (
    <div className="flex flex-col items-start gap-1">
      <Button
        variant={TRIGGER[kind]}
        size={size}
        disabled={disabled || done || cooling}
        onClick={() => {
          onClick?.()
          setOpen(true)
        }}
        data-action={kind}
      >
        <Icon size={16} aria-hidden="true" />
        {done ? (doneLabel ?? ACTION_LABEL[kind]) : ACTION_LABEL[kind]}
      </Button>
      {hideHint ? null : <span className="text-mono text-muted">{undoHint ?? UNDO[kind]}</span>}
      <Dialog
        open={open}
        title={confirmTitle[kind]}
        onClose={() => {
          if (busy) return
          setOpen(false)
          setError(null)
        }}
        initialFocus={cancelRef}
        footer={
          <>
            <Button ref={cancelRef} variant="secondary" onClick={() => setOpen(false)} disabled={busy}>
              {CANCEL}
            </Button>
            <Button variant={CONFIRM_VARIANT[kind]} onClick={() => void confirm()} disabled={busy || cooling} data-testid="confirm-action">
              {busy ? WORKING : CONFIRM_LABEL[kind]}
            </Button>
          </>
        }
      >
        <p>{body}</p>
        {error ? (
          <p role="alert" className="mt-3 rounded-sm border tint-warn px-3 py-2 text-meta">
            {error}
          </p>
        ) : null}
      </Dialog>
    </div>
  )
}
