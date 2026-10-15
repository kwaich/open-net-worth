import { cloneElement, isValidElement, type ReactNode } from 'react'
import { Info } from '../../ui/icons'

export interface FormFieldProps {
  label?: ReactNode
  /** One short line explaining the field — shown as an "i" icon next to the label
   *  (hover/focus reveals it) instead of a permanent line of body text. Use this
   *  for "what does this mean" explanations; reserve `help` for a live status
   *  message (e.g. a secret-reveal caption) that should stay visible. */
  info?: ReactNode
  help?: ReactNode
  error?: ReactNode
  required?: boolean
  htmlFor?: string
  span?: 1 | 'full'
  /** Reserve one help/error line so validation never shifts surrounding controls. */
  reserveMessageSpace?: boolean
  children: ReactNode
}

/**
 * FormField — the label + control + help/error wrapper. Unifies the two field
 * idioms (.profile-field stack vs .form-row label-wrap) behind one component.
 * Pass `htmlFor` matching the control's `id` so the label is tied to it; `help`
 * renders muted, `error` renders in --bad and takes precedence over help.
 *
 * a11y: when a describedBy id is derived (error or help present) the direct
 * child element receives aria-describedby pointing at that id so screen-readers
 * announce the text. aria-invalid is also set on the child when error is present.
 */
export function FormField({
  label,
  info,
  help,
  error,
  required,
  htmlFor,
  span = 1,
  reserveMessageSpace = false,
  children,
}: FormFieldProps) {
  const describedBy = error ? `${htmlFor}-error` : help ? `${htmlFor}-help` : undefined

  // Wire aria-describedby / aria-invalid onto the child control so screen-readers
  // associate the help/error text with the input — not just visually.
  const augmentedChild =
    describedBy && isValidElement(children)
      ? cloneElement(children as React.ReactElement<Record<string, unknown>>, {
          'aria-describedby': describedBy,
          ...(error ? { 'aria-invalid': true } : {}),
        })
      : children

  return (
    <div className={`ui-field${span === 'full' ? ' ui-field--full' : ''}`}>
      {label && (
        <span className="ui-field__label-row">
          <label className="form-label" htmlFor={htmlFor}>
            {label}
            {required && (
              <span className="ui-field__label-req" aria-hidden="true">
                *
              </span>
            )}
          </label>
          {info && (
            <button
              type="button"
              className="ui-field__info"
              aria-label={typeof info === 'string' ? info : 'More info'}
            >
              <Info size={12} strokeWidth={2} aria-hidden="true" />
              <span className="ui-field__info-tip" role="tooltip">
                {info}
              </span>
            </button>
          )}
        </span>
      )}
      {augmentedChild}
      {error ? (
        <p className="ui-field__error" id={describedBy} role="alert">
          {error}
        </p>
      ) : help ? (
        <p className="ui-field__help" id={describedBy}>
          {help}
        </p>
      ) : reserveMessageSpace ? (
        <span className="ui-field__message-space" aria-hidden="true" />
      ) : null}
    </div>
  )
}
