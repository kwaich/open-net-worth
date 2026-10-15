import type { InputHTMLAttributes } from 'react'
import { getDateFormat } from '../../utils/format'
import { FormattedDateField } from './FormattedDateField'

export interface DateFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  invalid?: boolean
  wrapperClassName?: string
  unstyled?: boolean
}

/** The one date input. With a Date-format preference set it types and shows dates in that
 *  format (FormattedDateField); on "Browser default" it is the native input, contained by an
 *  ancestor so iOS cannot paint or size it outside the field. */
export function DateField({
  invalid,
  className,
  wrapperClassName,
  unstyled = false,
  ...rest
}: DateFieldProps) {
  const format = getDateFormat()
  if (format) {
    return (
      <FormattedDateField
        format={format}
        invalid={invalid}
        className={className}
        wrapperClassName={wrapperClassName}
        unstyled={unstyled}
        {...rest}
      />
    )
  }
  const inputClasses = [
    unstyled ? '' : 'ui-input',
    invalid && !unstyled ? 'ui-input--invalid' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  // Only a field the kit draws is pinned to a text input's height (index.css): a caller's own
  // wrapper draws its own box around the input, and owns its height.
  const wrapperClasses = [
    'ui-datefield',
    unstyled || wrapperClassName ? '' : 'ui-datefield--boxed',
    wrapperClassName || '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <span className={wrapperClasses}>
      <input
        type="date"
        className={inputClasses || undefined}
        aria-invalid={invalid || undefined}
        // Lets CSS fade an empty date's mask, which otherwise looks like a date picked.
        data-empty={rest.value === '' || undefined}
        {...rest}
      />
    </span>
  )
}
