import type { InputHTMLAttributes } from 'react'

export interface NumberFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  invalid?: boolean
  min?: number
  max?: number
  step?: number | 'any'
}

/** NumberField — TextField pre-set to `type=number` with min/step/max baked in. */
export function NumberField({ invalid, className, ...rest }: NumberFieldProps) {
  const classes = ['ui-input', invalid ? 'ui-input--invalid' : '', className || '']
    .filter(Boolean)
    .join(' ')
  return <input type="number" className={classes} aria-invalid={invalid || undefined} {...rest} />
}
