import type { InputHTMLAttributes } from 'react'

export interface SwitchProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label?: string
  /** Short tooltip shown on the switch handle when disabled. */
  disabledReason?: string
  /** Denser 34×20 track with a bordered, translucent-accent style (Plan tab density). */
  dense?: boolean
}

/**
 * Toggle switch — semantically a checkbox, styled as a pill slider.
 * Renders an accessible <label>+<input type="checkbox"> pair.
 * The `label` prop is visually hidden (screen-reader only) when not provided.
 */
export function Switch({
  label,
  disabledReason,
  dense = false,
  className,
  id,
  disabled,
  ...rest
}: SwitchProps) {
  const classes = [
    'ui-switch',
    dense ? 'ui-switch--dense' : '',
    disabled ? 'ui-switch--disabled' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <label
      className={classes}
      htmlFor={id}
      title={disabled && disabledReason ? disabledReason : undefined}
    >
      <input type="checkbox" id={id} role="switch" disabled={disabled} {...rest} />
      <span className="ui-switch__track" aria-hidden="true">
        <span className="ui-switch__thumb" />
      </span>
      {label && <span className="ui-switch__label">{label}</span>}
    </label>
  )
}
