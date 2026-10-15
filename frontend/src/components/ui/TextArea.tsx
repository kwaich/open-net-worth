import { forwardRef } from 'react'
import type { TextareaHTMLAttributes } from 'react'

export interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  invalid?: boolean
  rows?: number
  /** Monospace, wraps anywhere, no resize handle — for codes, tokens and other machine text. */
  mono?: boolean
}

/** TextArea — styled multi-line input matching the house focus ring. Forwards its ref to the
 * native `<textarea>` (e.g. so a caller can focus it programmatically on reveal). */
export const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea(
  { invalid, mono, className, rows = 3, ...rest },
  ref,
) {
  const classes = [
    'ui-textarea',
    invalid ? 'ui-textarea--invalid' : '',
    mono ? 'ui-textarea--mono' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  return (
    <textarea
      ref={ref}
      className={classes}
      rows={rows}
      aria-invalid={invalid || undefined}
      {...rest}
    />
  )
})
