import { useEffect, useRef, useState, type CSSProperties, type InputHTMLAttributes } from 'react'
import { amountDraft, currencyMarks, parseAmount, thousandsReading } from './amountText'

interface MoneyFieldBaseProps extends Omit<
  InputHTMLAttributes<HTMLInputElement>,
  'type' | 'value' | 'onChange' | 'prefix'
> {
  formattedValue: string
  /** Drawn inside the field on the left (e.g. a currency symbol) — never part of the text. */
  prefix?: string
  suffix?: string
  invalid?: boolean
  /** Told when the field holds text it can't read as an amount (true) and when that ends —
   *  a readable commit, Escape, or the field going away (false). The field keeps the text and
   *  says why; its owner must not save the figure it had before, so it refuses to save. */
  onInvalidChange?(invalid: boolean): void
}

interface RequiredMoneyFieldProps extends MoneyFieldBaseProps {
  allowEmpty?: false
  value: number
  onValueChange(value: number): void
}

/** A field that may be left blank: clearing it commits `null` instead of flagging it. */
interface OptionalMoneyFieldProps extends MoneyFieldBaseProps {
  allowEmpty: true
  value: number | null
  onValueChange(value: number | null): void
}

export type MoneyFieldProps = RequiredMoneyFieldProps | OptionalMoneyFieldProps

/** An always-visible money input. Formatting is display-only; focused editing uses raw digits. */
export function MoneyField(props: MoneyFieldProps) {
  const {
    value,
    formattedValue,
    prefix,
    suffix,
    invalid: invalidProp,
    allowEmpty,
    className,
    onValueChange,
    onInvalidChange,
    onFocus,
    onBlur,
    onKeyDown,
    ...rest
  } = props
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(() => amountDraft(value))
  // The text last committed couldn't be read: it stays in `draft`, shown and flagged.
  const [invalid, setInvalidState] = useState(false)
  const reported = useRef(false)
  const reportTo = useRef(onInvalidChange)
  useEffect(() => {
    reportTo.current = onInvalidChange
  })
  const skipNextBlurCommit = useRef(false)
  // An autofocused field opens on its formatted figure, selected — the first keystroke
  // replaces it — instead of switching a prefilled "850,000" to raw digits before the user
  // has done anything. Only when that text reads back as the value, though: editing it in
  // place must never misread it. Any later focus edits raw digits as usual.
  const firstFocus = useRef(Boolean(rest.autoFocus))

  function setInvalid(bad: boolean) {
    setInvalidState(bad)
    if (reported.current === bad) return
    reported.current = bad
    reportTo.current?.(bad)
  }
  // The currency this field's figure is in, as it may be typed around the digits.
  const marks = currencyMarks(prefix)
  // A figure set from outside (a reset, a loaded scenario) supersedes unreadable text the field
  // was left holding: it shows the figure actually in use, not the stale text and its flag.
  const [shownFor, setShownFor] = useState(value)
  if (!Object.is(shownFor, value)) {
    setShownFor(value)
    if (invalid && !editing) {
      setDraft(amountDraft(value))
      setInvalidState(false)
    }
  }
  // …and its owner hears it is readable again (a commit already told it, synchronously).
  useEffect(() => {
    if (invalid || !reported.current) return
    reported.current = false
    reportTo.current?.(false)
  }, [invalid])
  // Going away with unreadable text: nothing is left to refuse a save over.
  useEffect(
    () => () => {
      if (reported.current) reportTo.current?.(false)
    },
    [],
  )

  function commit() {
    const normalized = draft.trim()
    setEditing(false)
    if (allowEmpty && normalized === '') {
      setInvalid(false)
      if (props.allowEmpty) props.onValueChange(null)
      return
    }
    const parsed = parseAmount(normalized, marks)
    if (parsed == null || parsed < 0) return setInvalid(true)
    setInvalid(false)
    onValueChange(parsed)
  }

  // Flagged while not being edited — typing a fix hides the flag until the next commit.
  const shownInvalid = invalid && !editing
  const isInvalid = Boolean(invalidProp || shownInvalid)
  const classes = ['ui-input', isInvalid ? 'ui-input--invalid' : '', className || '']
    .filter(Boolean)
    .join(' ')
  // The input reserves room for the prefix by its length (index.css) — "$" and "HK$" alike.
  const style = prefix ? ({ '--ui-money-prefix-chars': prefix.length } as CSSProperties) : undefined
  // A thousandfold-ambiguous entry is refused; the message names the figure it most likely meant.
  const meant = shownInvalid ? thousandsReading(draft, marks) : null

  return (
    <div className="ui-money-field money" style={style}>
      <div className="ui-money-field__box">
        {prefix && (
          <span className="ui-money-field__prefix" aria-hidden="true">
            {prefix}
          </span>
        )}
        <input
          {...rest}
          type="text"
          inputMode="decimal"
          className={classes}
          // Unreadable text stays on screen — never swapped for the last good figure.
          value={editing || invalid ? draft : formattedValue}
          // A FormField marks its errored child aria-invalid — keep that, never override it.
          aria-invalid={isInvalid || rest['aria-invalid'] || undefined}
          onFocus={(event) => {
            if (!invalid) setDraft(amountDraft(value))
            const opensFormatted =
              firstFocus.current && parseAmount(formattedValue, marks) === value
            firstFocus.current = false
            if (!opensFormatted) setEditing(true)
            event.currentTarget.select()
            onFocus?.(event)
          }}
          onChange={(event) => {
            // Typing after an Enter or Escape is new text — the blur that follows must commit it.
            skipNextBlurCommit.current = false
            setDraft(event.target.value)
            setEditing(true)
          }}
          onBlur={(event) => {
            if (skipNextBlurCommit.current) skipNextBlurCommit.current = false
            else commit()
            onBlur?.(event)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              skipNextBlurCommit.current = true
              commit()
            } else if (event.key === 'Escape') {
              skipNextBlurCommit.current = true
              setDraft(amountDraft(value))
              setInvalid(false)
              setEditing(false)
            }
            onKeyDown?.(event)
          }}
        />
        {suffix && (
          <span className="ui-money-field__suffix" aria-hidden="true">
            {suffix}
          </span>
        )}
      </div>
      {/* Only its own parse failure. A caller's `invalid` marks the field; the caller says why. */}
      {shownInvalid && (
        <span className="ui-money-field__error" role="alert">
          {meant ? `Did you mean ${meant}?` : 'Enter an amount of 0 or more.'}
        </span>
      )}
    </div>
  )
}
