import type { ReactNode } from 'react'

export interface RadioGroupOption {
  value: string
  label: ReactNode
  description?: ReactNode
  /** Rendered inline after the label, e.g. <Badge tone="neutral">Default</Badge>. */
  badge?: ReactNode
  disabled?: boolean
}

export interface RadioGroupProps {
  /** Shared `name` for the native radio inputs — groups them for native
   *  browser arrow-key navigation (free a11y). */
  name: string
  options: RadioGroupOption[]
  value: string
  onChange: (next: string) => void
  ariaLabel?: string
  /** Disables every option (e.g. while an eager save is in flight). */
  disabled?: boolean
}

/**
 * RadioGroup — exclusive choice with a plain-language description per option.
 * The single-select sibling of CheckboxGroup: one option per row (options read
 * as sentences, never a tag grid), native <input type="radio"> for real form
 * semantics and free arrow-key navigation. First caller: the Settings →
 * AI Model "Model routing" profile picker.
 */
export function RadioGroup({
  name,
  options,
  value,
  onChange,
  ariaLabel,
  disabled = false,
}: RadioGroupProps) {
  return (
    <div className="ui-radiogroup" role="radiogroup" aria-label={ariaLabel}>
      {options.map((o) => (
        <label key={o.value} className="ui-radiogroup__opt" htmlFor={`${name}-${o.value}`}>
          <input
            type="radio"
            id={`${name}-${o.value}`}
            name={name}
            value={o.value}
            checked={o.value === value}
            disabled={disabled || o.disabled}
            onChange={() => onChange(o.value)}
          />
          <span className="ui-radiogroup__opt-text">
            <span className="ui-radiogroup__opt-label">
              {o.label}
              {o.badge && <span className="ui-radiogroup__opt-badge">{o.badge}</span>}
            </span>
            {o.description && <span className="ui-radiogroup__opt-desc">{o.description}</span>}
          </span>
        </label>
      ))}
    </div>
  )
}
