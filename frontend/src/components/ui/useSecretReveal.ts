import { useCallback, useEffect, useState } from 'react'

/**
 * useSecretReveal — the eye on a secret field that ALSO reveals the SAVED value.
 *
 * A plain `PasswordField` unmasks only what you just typed, which is useless above an empty
 * box holding a saved credential: the operator pasted that key in and cannot read it back
 * anywhere in the product. This hook makes the SAME eye do both jobs, so there is one
 * control per field rather than an eye plus a "Reveal saved value" button:
 *
 *   * empty box + a saved value  → the eye fetches the stored value from the server and puts
 *     it in the box, editable in place;
 *   * anything typed             → the eye is the ordinary local mask toggle;
 *   * hide, value unedited       → back to the clean saved state (an empty box, so Save's
 *     "blank keeps the stored value" rule still applies and nothing is rewritten);
 *   * hide, value edited         → the edit stays and replaces the secret on Save.
 *
 * That last pair is the whole reason `revealed` is tracked: a revealed-then-untouched field
 * must not count as a change, or opening the eye would silently rewrite every secret the
 * user merely looked at (and would arm the modal's Save just for looking — see `pristine`).
 *
 * Presentation-agnostic on purpose. Spread `fieldProps` onto a `ui/PasswordField`, or read
 * `fieldProps.shown` / `fieldProps.onToggleShown` directly for a hand-rolled eye over the
 * design-handoff `rd-input`/`rd-textarea` elements (`connectors/McpConnectionFields`), where
 * the same state machine drives a plain textarea that is never masked at all.
 *
 * `reveal` resolves with the stored value or rejects; the caller supplies it (typically
 * `revealConnectorSecret(connectorId, field)`), so nothing about transport lives here.
 */
export type SecretRevealState = 'idle' | 'loading' | 'shown' | 'error'

export interface UseSecretRevealOptions {
  /** Whether the server holds a value for this field (`has_key` / `has_token` / …). */
  saved: boolean
  /** The field's current value — '' means "untouched, keep what is saved". */
  value: string
  /** Called with the revealed value, and with '' when an unedited reveal is hidden. */
  onChange: (value: string) => void
  /** Fetch the stored value. Rejects (or resolves empty) → the error state. */
  reveal: () => Promise<string>
}

export interface SecretReveal {
  state: SecretRevealState
  /** True while the box holds a revealed value the user has NOT edited. Looking at a key is
   *  not changing it, so a form folds this back to '' before its dirty comparison — see
   *  `connectors/formSave.unrevealed`. */
  pristine: boolean
  /** Spread onto `PasswordField` (or read directly for a hand-rolled eye). `loading` is
   *  true only during the server round-trip a reveal makes — shown as a spinner in place
   *  of the eye icon, so the wait is visible without a separate caption line. */
  fieldProps: { shown?: boolean; onToggleShown?: () => void; loading?: boolean }
  /** One sentence describing what the field is doing — pass as `FormField.help`. Empty
   *  when nothing is saved, so the form's own first-time instructions show instead. */
  caption: string
  /** Non-empty only when a reveal failed — pass as `FormField.error`. */
  error: string
}

export function useSecretReveal({
  saved,
  value,
  onChange,
  reveal,
}: UseSecretRevealOptions): SecretReveal {
  const [state, setState] = useState<SecretRevealState>('idle')
  const [revealed, setRevealed] = useState('')

  // 'shown' over an empty box is impossible UI — the eye would read open while the masked
  // placeholder shows, and the next tap would be a no-op hide. Reached whenever the value is
  // cleared out from under us: a successful Save, or the user selecting all and deleting.
  useEffect(() => {
    if (state === 'shown' && value === '') {
      setState('idle')
      setRevealed('')
    }
  }, [state, value])

  const doReveal = useCallback(async () => {
    setState('loading')
    try {
      const secret = await reveal()
      if (!secret) {
        setState('error')
        return
      }
      setRevealed(secret)
      onChange(secret)
      setState('shown')
    } catch {
      setState('error')
    }
  }, [reveal, onChange])

  const doHide = useCallback(() => {
    setState('idle')
    // Unedited → back to blank, which is what "keep the saved value" looks like on Save.
    // Edited → the typed value stays and replaces the secret.
    if (value === revealed) onChange('')
    setRevealed('')
  }, [value, revealed, onChange])

  // The parent owns the eye only while there is a server round-trip to make (a saved value
  // and an untouched box) or a revealed value on screen. Otherwise `PasswordField`'s own
  // local toggle handles it, which is the right behaviour for a freshly typed secret.
  const serverEye = (saved && value === '') || state === 'shown'
  const fieldProps = serverEye
    ? {
        shown: state === 'shown',
        onToggleShown: state === 'shown' ? doHide : doReveal,
        loading: state === 'loading',
      }
    : {}

  return {
    state,
    pristine: state === 'shown' && value === revealed,
    fieldProps,
    caption: '',
    error: errorText(state),
  }
}

function errorText(state: SecretRevealState): string {
  return state === 'error' ? 'Could not read the saved value. Try again.' : ''
}
