import { ChevronRight } from 'lucide-react'
import { useState } from 'react'
import type { Automation } from '../../../api/automations'
import { AutomationCard } from './AutomationCard'
import type { AutomationOptions } from './AutomationTriggerSummary'

/** Automations whose schedule ran out (`status: completed`) — collapsed under the live list so
 *  a pile of spent one-shots never crowds what is still on watch. A search opens it, since a
 *  match hidden behind a closed toggle reads as "no match". */
export function AutomationFinishedSection({
  automations,
  options,
  searching,
  busyId,
  onOpen,
  onRunNow,
  onEdit,
  onDuplicate,
  onDelete,
  onClear,
}: {
  automations: Automation[]
  options?: AutomationOptions
  searching: boolean
  busyId?: string | null
  onOpen: (automation: Automation) => void
  onRunNow: (automation: Automation) => void
  onEdit: (automation: Automation) => void
  onDuplicate: (automation: Automation) => void
  onDelete: (automation: Automation) => void
  onClear: () => void
}) {
  const [open, setOpen] = useState(false)
  if (automations.length === 0) return null
  const expanded = open || searching
  return (
    <section className="a3-done-section" aria-labelledby="automation-finished-heading">
      <div className="a3-sec-label">
        <button
          type="button"
          id="automation-finished-heading"
          className="a3-done-toggle"
          aria-expanded={expanded}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight
            className={`a3-done-chevron${expanded ? ' open' : ''}`}
            size={12}
            strokeWidth={2}
            aria-hidden="true"
          />
          Finished · {automations.length}
        </button>
        <span className="rule" />
        <button type="button" className="a3-linkbtn a3-done-clear" onClick={onClear}>
          Clear finished
        </button>
      </div>
      {expanded && (
        <div className="a3-list">
          {automations.map((automation) => (
            <AutomationCard
              key={automation.id}
              automation={automation}
              options={options}
              busy={busyId === automation.id}
              onOpen={() => onOpen(automation)}
              onRunNow={() => onRunNow(automation)}
              onToggle={() => onEdit(automation)}
              onEdit={() => onEdit(automation)}
              onDuplicate={() => onDuplicate(automation)}
              onDelete={() => onDelete(automation)}
            />
          ))}
        </div>
      )}
    </section>
  )
}
