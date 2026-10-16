import { Link } from 'react-router-dom'

interface EmptyStateProps {
  title: string
  action?: { label: string; to?: string; onClick?: () => void }
  className?: string
}

const ACTION = 'inline-flex min-h-9 items-center rounded-sm border border-border-strong bg-surface px-3.5 text-body text-text hover:bg-surface-2 max-md:min-h-11'

/** One sentence and at most one action, left-aligned. No illustration, no dead ends. */
export function EmptyState({ title, action, className = '' }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-start gap-3 border border-border bg-surface px-6 py-6 max-md:px-4 ${className}`} data-testid="empty-state">
      <p className="text-body text-text">{title}</p>
      {action ? (
        action.to ? (
          <Link to={action.to} className={ACTION}>
            {action.label}
          </Link>
        ) : (
          <button type="button" onClick={action.onClick} className={ACTION}>
            {action.label}
          </button>
        )
      ) : null}
    </div>
  )
}
