import type { InputHTMLAttributes } from 'react'
import { DateField } from './DateField'

export interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  invalid?: boolean
}

/** TextField — styled text input matching the house focus ring. */
export function TextField({ invalid, className, type = 'text', ...rest }: TextFieldProps) {
  if (type === 'date') {
    return <DateField invalid={invalid} className={className} {...rest} />
  }
  const classes = ['ui-input', invalid ? 'ui-input--invalid' : '', className || '']
    .filter(Boolean)
    .join(' ')
  return <input type={type} className={classes} aria-invalid={invalid || undefined} {...rest} />
}
