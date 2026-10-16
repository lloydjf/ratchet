import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

interface EmptyStateProps {
  title: string
  action?: { label: string; to?: string; onClick?: () => void }
  icon?: ReactNode
  className?: string
}

/** One sentence and at most one action (no dead ends). */
export function EmptyState({ title, action, icon, className = '' }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-start gap-4 rounded-card border border-dashed border-border-strong bg-surface px-6 py-8 sm:flex-row sm:items-center sm:justify-between ${className}`} data-testid="empty-state">
      <div className="flex items-center gap-3">
        {icon ? <span className="text-muted" aria-hidden="true">{icon}</span> : null}
        <p className="text-body text-text">{title}</p>
      </div>
      {action ? (
        action.to ? (
          <Link to={action.to} className="inline-flex min-h-11 items-center rounded-sm border border-border-strong bg-surface-2 px-4 text-body font-medium text-text hover:bg-bg">
            {action.label}
          </Link>
        ) : (
          <button type="button" onClick={action.onClick} className="inline-flex min-h-11 items-center rounded-sm border border-border-strong bg-surface-2 px-4 text-body font-medium text-text hover:bg-bg">
            {action.label}
          </button>
        )
      ) : null}
    </div>
  )
}
