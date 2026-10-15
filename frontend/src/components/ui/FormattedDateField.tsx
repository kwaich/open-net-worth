import { useRef, useState, type ChangeEvent, type InputHTMLAttributes } from 'react'
import {
  fDate,
  numericDatePlaceholder,
  parseNumericDate,
  type DateFormat,
} from '../../utils/format'

export interface FormattedDateFieldProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'type'
> {
  format: DateFormat
  invalid?: boolean
  wrapperClassName?: string
  unstyled?: boolean
}

// A native date input writes its own segments in the BROWSER's order and no page can change it,
// so with a Date-format preference set a text field parses the user's own order instead, and
// the calendar button opens a hidden native input for the picker. Callers read only
// `e.target.value` (always "YYYY-MM-DD"), exactly as they do from the native input.
const asChange = (value: string, name?: string) =>
  ({
    target: { value, name },
    currentTarget: { value, name },
  }) as unknown as ChangeEvent<HTMLInputElement>

export function FormattedDateField({
  format,
  invalid,
  className,
  wrapperClassName,
  unstyled = false,
  value,
  defaultValue,
  onChange,
  onBlur,
  min,
  max,
  disabled,
  ...rest
}: FormattedDateFieldProps) {
  const [own, setOwn] = useState(String(defaultValue ?? ''))
  const [draft, setDraft] = useState<string | null>(null)
  const native = useRef<HTMLInputElement>(null)
  const iso = value !== undefined ? String(value) : own
  const text = draft ?? (iso ? fDate(iso, 'numeric') : '')
  const unreadable = draft !== null && draft.trim() !== '' && !parseNumericDate(draft, format)

  const commit = (next: string) => {
    setOwn(next)
    onChange?.(asChange(next, rest.name))
  }
  const openPicker = () => {
    const el = native.current
    if (!el) return
    try {
      el.showPicker()
    } catch {
      el.focus()
      el.click()
    }
  }

  const inputClasses = [
    unstyled ? '' : 'ui-input',
    (invalid || unreadable) && !unstyled ? 'ui-input--invalid' : '',
    className || '',
  ]
    .filter(Boolean)
    .join(' ')
  const wrapperClasses = [
    'ui-datefield',
    'ui-datefield--typed',
    unstyled || wrapperClassName ? '' : 'ui-datefield--boxed',
    wrapperClassName || '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <span className={wrapperClasses}>
      <input
        type="text"
        inputMode="numeric"
        autoComplete="off"
        className={inputClasses || undefined}
        placeholder={numericDatePlaceholder(format)}
        aria-invalid={invalid || unreadable || undefined}
        disabled={disabled}
        value={text}
        onChange={(e) => {
          setDraft(e.target.value)
          if (e.target.value.trim() === '') commit('')
          else {
            const parsed = parseNumericDate(e.target.value, format)
            if (parsed) commit(parsed)
          }
        }}
        onBlur={(e) => {
          setDraft(null)
          onBlur?.(e)
        }}
        {...rest}
      />
      <button
        type="button"
        className="ui-datefield__picker"
        aria-label="Pick a date"
        disabled={disabled}
        onClick={openPicker}
      >
        <svg
          viewBox="0 0 16 16"
          width="14"
          height="14"
          fill="none"
          stroke="currentColor"
          aria-hidden
        >
          <rect x="2" y="3" width="12" height="11" rx="2" strokeWidth="1.4" />
          <path d="M2 6.5h12M5.5 1.8v2.4M10.5 1.8v2.4" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
      </button>
      <input
        ref={native}
        type="date"
        className="ui-datefield__native"
        tabIndex={-1}
        aria-hidden
        value={iso}
        min={min}
        max={max}
        disabled={disabled}
        onChange={(e) => {
          setDraft(null)
          commit(e.target.value)
        }}
      />
    </span>
  )
}
