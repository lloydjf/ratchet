import { CircleAlert } from 'lucide-react'
import { isApiError } from '../api/client'
import { apiErrorCopy, RETRY } from '../copy'
import { Button } from './Button'

interface ErrorStateProps {
  error: unknown
  onRetry?: () => void
  className?: string
}

/**
 * The registry's copy for the code (the server's sentence for 429 / 503 not_ready): the
 * problem and what it means for the edit, then the one option left, a retry where it helps.
 */
export function ErrorState({ error, onRetry, className = '' }: ErrorStateProps) {
  const code = isApiError(error) ? error.code : 'network'
  const requestId = isApiError(error) ? error.requestId : null
  const retry = onRetry && code !== 'not_public' && code !== 'not_found'
  return (
    <div role="alert" className={`flex flex-col items-start gap-3 border border-[color-mix(in_srgb,var(--warn)_55%,var(--border))] bg-surface px-6 py-5 max-md:px-4 ${className}`} data-testid="error-state" data-code={code}>
      <p className="flex items-baseline gap-2 text-body text-text">
        <CircleAlert size={14} strokeWidth={2.25} aria-hidden="true" className="relative top-0.5 shrink-0 text-warn-ink" />
        <span>{apiErrorCopy(isApiError(error) ? error : null)}</span>
      </p>
      {requestId ? <p className="font-mono text-mono text-muted">Request {requestId}</p> : null}
      {retry ? (
        <Button onClick={onRetry} size="sm">
          {RETRY}
        </Button>
      ) : null}
    </div>
  )
}
