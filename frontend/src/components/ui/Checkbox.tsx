import type { InputHTMLAttributes, ReactNode } from 'react'

export interface CheckboxProps extends InputHTMLAttributes<HTMLInputElement> {
  label: ReactNode
  description?: ReactNode
}

/**
 * Checkbox — a single labeled checkbox. The checkbox aligns to the top of the
 * label (flex-start) so it lines up with the name, not the middle of a wrapped
 * description (per the "List rows" convention).
 */
export function Checkbox({ label, description, className, disabled, id, ...rest }: CheckboxProps) {
  const classes = ['ui-check', disabled ? 'ui-check--disabled' : '', className || '']
    .filter(Boolean)
    .join(' ')
  return (
    <label className={classes} htmlFor={id}>
      <input type="checkbox" id={id} disabled={disabled} {...rest} />
      <span className="ui-check__text">
        <span className="ui-check__label">{label}</span>
        {description && <span className="ui-check__desc">{description}</span>}
      </span>
    </label>
  )
}
