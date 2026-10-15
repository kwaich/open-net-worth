import type { SelectHTMLAttributes } from 'react'

export interface SelectOption {
  value: string
  label: string
  disabled?: boolean
}

export interface SelectOptGroup {
  group: string
  options: SelectOption[]
}

/** A flat list of options OR a list of named groups (optgroup). Mixed arrays are not supported. */
export type SelectOptionOrGroup = SelectOption | SelectOptGroup

function isGroup(item: SelectOptionOrGroup): item is SelectOptGroup {
  return (item as SelectOptGroup).group !== undefined
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  options: SelectOption[] | SelectOptionOrGroup[]
  placeholder?: string
  invalid?: boolean
  /** Denser 13px field on --bg-2 with a custom chevron (Plan tab density). */
  dense?: boolean
}

/** Select — styled dropdown; `placeholder` renders a disabled empty option.
 *
 * When `options` contains `{group, options[]}` entries they are rendered as HTML
 * `<optgroup>` elements, enabling multi-provider grouped model pickers. A mixed
 * array (some groups, some flat) is not supported.
 */
export function Select({
  options,
  placeholder,
  invalid,
  dense = false,
  className,
  value,
  ...rest
}: SelectProps) {
  const classes = [
    'ui-select',
    dense ? 'ui-select--dense' : '',
    invalid ? 'ui-select--invalid' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')

  const firstOption = options[0]
  const hasGroups = firstOption !== undefined && isGroup(firstOption)

  return (
    <select className={classes} value={value} aria-invalid={invalid || undefined} {...rest}>
      {placeholder !== undefined && (
        <option value="" disabled>
          {placeholder}
        </option>
      )}
      {hasGroups
        ? (options as SelectOptGroup[]).map((g) => (
            <optgroup key={g.group} label={g.group}>
              {g.options.map((o) => (
                <option key={o.value} value={o.value} disabled={o.disabled}>
                  {o.label}
                </option>
              ))}
            </optgroup>
          ))
        : (options as SelectOption[]).map((o) => (
            <option key={o.value} value={o.value} disabled={o.disabled}>
              {o.label}
            </option>
          ))}
    </select>
  )
}
