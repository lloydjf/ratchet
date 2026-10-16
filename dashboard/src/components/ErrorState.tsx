import { AlertTriangle } from 'lucide-react'
import { isApiError } from '../api/client'
import { apiErrorCopy, RETRY } from '../copy'
import { Button } from './Button'

interface ErrorStateProps {
  error: unknown
  onRetry?: () => void
  className?: string
}

/** Code-mapped copy (the server's sentence for 429 / 503 not_ready) plus a retry where one is meaningful. */
export function ErrorState({ error, onRetry, className = '' }: ErrorStateProps) {
  const code = isApiError(error) ? error.code : 'network'
  const requestId = isApiError(error) ? error.requestId : null
  return (
    <div role="alert" className={`flex flex-col gap-4 rounded-card border tint-warn px-5 py-5 sm:flex-row sm:items-center sm:justify-between ${className}`} data-testid="error-state" data-code={code}>
      <div className="flex items-start gap-3">
        <AlertTriangle size={20} aria-hidden="true" className="mt-0.5 shrink-0" />
        <div>
          <p className="text-body text-text">{apiErrorCopy(isApiError(error) ? error : null)}</p>
          {requestId ? <p className="mt-1 font-mono text-mono text-muted">Request {requestId}</p> : null}
        </div>
      </div>
      {onRetry && code !== 'not_public' && code !== 'not_found' ? (
        <Button onClick={onRetry} variant="secondary">
          {RETRY}
        </Button>
      ) : null}
    </div>
  )
}
