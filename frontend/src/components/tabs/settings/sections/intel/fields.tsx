// Shared bits for Settings › Intelligence panels.
import type { MonitoringConfig } from '../../../../intel/monitoringConfig'

/** Save a config change, then toast `message` (or a failure note). */
export type Save = (changes: Partial<MonitoringConfig>, message: string) => void

export interface PanelProps {
  cfg: MonitoringConfig
  save: Save
}

/** A number field that commits on blur (or Enter), clamped to [min, max]. An empty or invalid
 *  entry snaps back to the saved value instead of saving anything. */
export function NumberInput({
  id,
  value,
  min,
  max,
  step = 1,
  integer = true,
  disabled,
  onCommit,
}: {
  id: string
  value: number
  min: number
  max: number
  step?: number
  integer?: boolean
  disabled?: boolean
  onCommit: (next: number) => void
}) {
  return (
    <input
      id={id}
      className="rd-input"
      type="number"
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      defaultValue={value}
      key={`${id}:${value}`}
      onBlur={(e) => {
        const raw = integer ? parseInt(e.target.value, 10) : parseFloat(e.target.value)
        if (Number.isNaN(raw)) {
          e.target.value = String(value)
          return
        }
        const next = Math.max(min, Math.min(max, raw))
        e.target.value = String(next)
        if (next !== value) onCommit(next)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
      }}
    />
  )
}
