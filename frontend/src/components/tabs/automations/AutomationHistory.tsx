import { useEffect, useRef } from 'react'
import type { AutomationRun, AutomationRunStatus } from '../../../api/automations'
import { ChevronRight, Hand } from 'lucide-react'
import { fDateTime } from '../../../utils/format'
import { money } from './AutomationCostPanel'

export function AutomationHistory({
  runs,
  selectedRunId,
  onSelectRun,
  emptyMessage = 'Hasn’t run yet. Hit Run now to start it.',
}: {
  runs: AutomationRun[]
  selectedRunId: string | null
  onSelectRun: (run: AutomationRun) => void
  emptyMessage?: string
}) {
  // Keep the open run visible inside the scroller — following a deep link, or stepping
  // through runs with the keyboard, otherwise lands on a row nowhere near the viewport.
  const selectedRow = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    // Optional-called: jsdom has no scrollIntoView, and this is a nicety — never let a
    // missing scroll primitive take the history list down with it.
    selectedRow.current?.scrollIntoView?.({ block: 'nearest' })
  }, [selectedRunId])

  if (runs.length === 0) {
    return <div className="a3-history-empty">{emptyMessage}</div>
  }
  // Nothing open → the list IS the page, so it runs full length. Open a run and it becomes
  // a scroller about five rows tall, so the card below stays on screen — but it still holds
  // EVERY run and scrolls the selected one into view. Hiding the rest behind a "close this
  // to see them" line took the list away to make room for one of its own entries.
  return (
    <div
      className={`a3-hist${selectedRunId ? ' is-windowed' : ''}`}
      role="list"
      aria-label="Automation run history"
    >
      {runs.map((run) => (
        <button
          key={run.id}
          type="button"
          role="listitem"
          ref={selectedRunId === run.id ? selectedRow : undefined}
          className={`a3-hist-row${selectedRunId === run.id ? ' on' : ''}`}
          onClick={() => onSelectRun(run)}
        >
          <span
            className={`rd-dot ${run.status === 'failed' ? 'bad' : run.status === 'running' ? 'run' : run.status === 'waiting' ? 'warn' : run.status === 'cancelled' ? '' : 'good'}`}
            aria-hidden="true"
          />
          <span className="when">{fDateTime(run.started_at, 'short', 'always')}</span>
          <span className="note">{statusLabel(run.status)}</span>
          {run.status === 'waiting' && (
            <span className="a3-hand" title="Waiting on you">
              <Hand size={13} aria-hidden="true" />
            </span>
          )}
          {run.status === 'running' && <span className="a3-live" title="Running now" />}
          <span className="cost">
            {run.status === 'running' || run.cost_usd == null ? '' : money(run.cost_usd)}
          </span>
          <ChevronRight size={13} className="a3-hist-chevron" aria-hidden="true" />
        </button>
      ))}
    </div>
  )
}

export function statusLabel(status: AutomationRunStatus) {
  // "Stopped", not "Cancelled": it names what the user did, not something that happened to the run.
  return {
    completed: 'Completed',
    running: 'Running',
    waiting: 'Needs you',
    failed: 'Failed',
    cancelled: 'Stopped',
  }[status]
}
