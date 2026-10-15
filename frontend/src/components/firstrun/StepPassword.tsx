// First run, screen 3 — an optional password.
//
// Framed by what it is actually FOR. Auth is on by default but loopback is trusted
// (AUTH_TRUST_LOOPBACK), so on the machine running the app a password buys nothing; it matters
// the moment you reach this instance from another device.
//
// A book that already has a password (Settings › Data › Reset keeps it — see the reset dialog)
// gets no fields here at all: setting one would now need the CURRENT password, which this screen
// has no way to ask for. It says so and offers Finish; changing it lives in Settings › Security.
//
// Skip is a real, equally-weighted choice rather than a ghost link — a skipped step reads as
// "did I just break something?", a chosen one does not (design spec §5.1).
//
// The row shape matches every other step: field + note inside `firstrun-row__value`, so the
// eye, the hint and Continue all sit on the same axis as the rest of the flow. "8 characters
// minimum" used to live as a sidebar note that never became an error — a password typed too
// short just silently failed on submit with no visible cause until Set & finish was pressed.

import { useEffect, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { authFetch } from '../../api/client'
import { Button, PasswordField, Stack, Text } from '../ui'

export interface StepPasswordProps {
  onDone: () => void
  onAuthChanged?: () => void
}

const MIN_LENGTH = 8

/** Whether this book already holds a password; `null` until the server has answered. A failed read
 *  falls back to "none" — the form, whose submit is still guarded server-side. */
function usePasswordSet(): boolean | null {
  const [passwordSet, setPasswordSet] = useState<boolean | null>(null)
  useEffect(() => {
    let live = true
    authFetch('/api/auth/settings')
      .then((res) => (res.ok ? res.json() : null))
      .then((s) => live && setPasswordSet(!!s?.password_set))
      .catch(() => live && setPasswordSet(false))
    return () => {
      live = false
    }
  }, [])
  return passwordSet
}

export function StepPassword({ onDone, onAuthChanged }: StepPasswordProps) {
  const passwordSet = usePasswordSet()
  // The form shows at once — a fresh book has no password, and rendering nothing until the
  // server answered flashed an empty step between Lucius and the password. A book that DOES
  // have one (a restored backup) swaps to the notice when the answer lands.
  if (passwordSet) return <PasswordAlreadySet onDone={onDone} />
  return <SetPasswordForm onDone={onDone} onAuthChanged={onAuthChanged} />
}

function PasswordAlreadySet({ onDone }: { onDone: () => void }) {
  return (
    <Stack gap="--space-7">
      <Text as="p" tone="muted" className="firstrun-lead">
        This book already has a password. You can change it any time in Settings &rsaquo; Security.
      </Text>
      <div className="firstrun-actions">
        <Button variant="primary" onClick={onDone} autoFocus>
          Finish
        </Button>
      </div>
    </Stack>
  )
}

function SetPasswordForm({ onDone, onAuthChanged }: StepPasswordProps) {
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Shown once the field has actually been left short, not while it is merely empty (empty
  // means "skip", which is a valid answer) or still being typed into.
  const [touchedPassword, setTouchedPassword] = useState(false)

  const tooShort = touchedPassword && password.length > 0 && password.length < MIN_LENGTH
  // A string comparison, not a server round trip — there is nothing to wait for, so this is
  // live on every keystroke rather than gated behind blur or a submit click. Once it disagrees,
  // Set & finish is unusable until it agrees again.
  const mismatch = confirm.length > 0 && password !== confirm
  const mismatchError = mismatch ? 'Those two passwords do not match.' : null
  // An untyped confirm field is not a match — password !== '' is true, same as any other
  // disagreement. Typed-and-equal is the only state that may submit.
  const readyToSubmit = password.length > 0 && confirm.length > 0 && password === confirm

  async function submit() {
    setError(null)
    // Kept exactly as typed, spaces included — but a password of only spaces is no password.
    if (!password.trim()) {
      setError('A password can’t be only spaces.')
      return
    }
    if (password.length < MIN_LENGTH) {
      setTouchedPassword(true)
      return
    }
    if (password !== confirm) return
    setBusy(true)
    try {
      const res = await authFetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ new_password: password }),
      })
      if (!res.ok) {
        const data = await res.json().catch(() => null)
        setError(data?.detail || `Could not set password (${res.status}). Try again.`)
        return
      }
      onAuthChanged?.()
      setPassword('')
      setConfirm('')
      onDone()
    } catch {
      setError('Could not reach the server. Check it is running and try again.')
    } finally {
      setBusy(false)
    }
  }

  function onKeyDown(e: KeyboardEvent) {
    // submit() itself is the guard (short password, empty/mismatched confirm all bail there) —
    // Enter always attempts it so a short password typed and Entered still surfaces its hint,
    // the same as clicking Set & finish would if it weren't disabled.
    if (e.key === 'Enter' && !busy) void submit()
  }

  return (
    <Stack gap="--space-7">
      <Text as="p" tone="muted" className="firstrun-lead">
        Only needed if you expose this instance beyond this machine — a public URL, a reverse proxy,
        another device on your network. Set one any time in Settings &rsaquo; Security.
      </Text>

      <div className="firstrun-rows firstrun-rows--password">
        <div className="firstrun-row">
          <label className="firstrun-row__label" htmlFor="fr-pw">
            Password
          </label>
          <span className="firstrun-row__value">
            <PasswordField
              id="fr-pw"
              autoComplete="new-password"
              autoFocus
              value={password}
              invalid={tooShort}
              onChange={(e) => {
                setPassword(e.target.value)
                setError(null)
              }}
              onBlur={() => setTouchedPassword(true)}
              onKeyDown={onKeyDown}
              placeholder="Leave blank to skip"
            />
            <Text
              as="span"
              size="xs"
              tone={tooShort ? 'bad' : 'faint'}
              className="firstrun-row__note"
            >
              {tooShort
                ? `At least ${MIN_LENGTH} characters — ${MIN_LENGTH - password.length} more to go.`
                : `${MIN_LENGTH} characters minimum`}
            </Text>
          </span>
        </div>
        <div className="firstrun-row">
          <label className="firstrun-row__label" htmlFor="fr-pw2">
            Confirm
          </label>
          <span className="firstrun-row__value">
            <PasswordField
              id="fr-pw2"
              autoComplete="new-password"
              value={confirm}
              invalid={mismatch}
              onChange={(e) => {
                setConfirm(e.target.value)
                setError(null)
              }}
              onKeyDown={onKeyDown}
              placeholder="Repeat the password above"
            />
          </span>
        </div>
      </div>

      <div className="firstrun-actions">
        {(error || mismatchError) && (
          <span className="firstrun-actions__error">{error || mismatchError}</span>
        )}
        <Button variant="ghost" onClick={onDone} disabled={busy}>
          Skip
        </Button>
        {/* Disabled until both fields are typed and agree — empty (Skip is the way past with
            nothing typed), a mismatch, and an as-yet-unconfirmed password (typed, confirm still
            blank) are all the same "not ready" state, live, not discovered by clicking through
            to a submit-time error. */}
        <Button
          variant="primary"
          loading={busy}
          disabled={!readyToSubmit}
          onClick={() => void submit()}
        >
          Set &amp; finish
        </Button>
      </div>
    </Stack>
  )
}
