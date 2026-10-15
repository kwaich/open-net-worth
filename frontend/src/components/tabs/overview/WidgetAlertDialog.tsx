// Overview v2 — the per-widget "Set alert…" dialog (kebab menu → Set alert…).
// A small confirm dialog: "Notify me when it [op] [value]" → `alert = {op,
// value}` on the instance. UI-only front-end mock — no evaluation engine
// behind it yet, matching the handoff's own documented scope (see the ADR's
// decision 6 / PARITY_AND_GAPS.md).

import { useState } from 'react'
import { Button, Modal, NumberField, Select } from '../../ui'
import type { WidgetAlert } from './types'

const OPS: Array<{ value: WidgetAlert['op']; label: string }> = [
  { value: '>', label: 'is greater than' },
  { value: '<', label: 'is less than' },
  { value: '>=', label: 'is at least' },
  { value: '<=', label: 'is at most' },
  { value: '==', label: 'equals' },
]

export interface WidgetAlertDialogProps {
  open: boolean
  onClose: () => void
  alert: WidgetAlert | null | undefined
  onSave: (alert: WidgetAlert | null) => void
}

export function WidgetAlertDialog({ open, onClose, alert, onSave }: WidgetAlertDialogProps) {
  const [op, setOp] = useState<WidgetAlert['op']>(alert?.op || '>')
  const [value, setValue] = useState<string>(alert?.value != null ? String(alert.value) : '')

  const save = () => {
    const n = Number(value)
    if (!value.trim() || !isFinite(n)) return
    onSave({ op, value: n })
    onClose()
  }
  const remove = () => {
    onSave(null)
    onClose()
  }

  return (
    <Modal open={open} onClose={onClose} title="Set alert" size="sm">
      <div className="ov-alert-dialog">
        <p className="ov-alert-copy">Lucius pings you when this crosses your threshold.</p>
        <div className="ov-alert-row">
          <span>Notify me when it</span>
          <Select
            aria-label="Comparison"
            value={op}
            onChange={(e) => setOp(e.target.value as WidgetAlert['op'])}
            options={OPS.map((o) => ({ value: o.value, label: o.label }))}
          />
          <NumberField
            aria-label="Threshold value"
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="0"
          />
        </div>
        <div className="ov-alert-actions">
          {alert && (
            <Button size="sm" variant="ghost" onClick={remove}>
              Remove alert
            </Button>
          )}
          <Button size="sm" variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" variant="primary" onClick={save} disabled={!value.trim()}>
            Save
          </Button>
        </div>
      </div>
    </Modal>
  )
}
