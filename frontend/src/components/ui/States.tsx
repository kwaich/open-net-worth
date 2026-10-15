import type { ReactNode } from 'react'
import { Button } from './Button'

export interface EmptyStateProps {
  title: ReactNode
  body?: ReactNode
  action?: ReactNode
  icon?: ReactNode
  /** The view IS the page (a screen with nothing in it yet): centre it in the space rather
   *  than leaving it near the top. Leave off inside a card, list or modal. */
  fill?: boolean
}

/** EmptyState — the "no data yet" panel with an optional primary CTA. */
export function EmptyState({ title, body, action, icon, fill = false }: EmptyStateProps) {
  return (
    <div className={fill ? 'ui-state ui-state--fill' : 'ui-state'}>
      {icon && <div className="ui-state__icon">{icon}</div>}
      <div className="ui-state__title">{title}</div>
      {body && <div className="ui-state__body">{body}</div>}
      {action}
    </div>
  )
}

export interface LoadingProps {
  label?: string
}

/** Loading — centered spinner + optional label. */
export function Loading({ label = 'Loading…' }: LoadingProps) {
  return (
    <div className="ui-state" role="status" aria-live="polite">
      <div className="ui-state__spinner" aria-hidden="true" />
      {label && <div className="ui-state__body">{label}</div>}
    </div>
  )
}

export interface ErrorStateProps {
  message: ReactNode
  title?: ReactNode
  onRetry?: () => void
}

/** ErrorState — error message with an optional Retry. */
export function ErrorState({ message, title, onRetry }: ErrorStateProps) {
  return (
    <div className="ui-state" role="alert">
      <div className="ui-state__title ui-state__error-title">{title ?? 'Something went wrong'}</div>
      <div className="ui-state__body">{message}</div>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}
