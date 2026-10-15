import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, PasswordField, useSecretReveal } from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// Lunch Money connector config over /api/lunch-money/* (per-provider plane), mirroring
// PlaidSection's secret-reveal pattern: the access token is something the operator TYPED
// into Lunch Money's own Settings -> Developers page and pasted here, so — unlike
// SimpleFIN's machine-minted access URL — it IS revealable through the shared eye
// (GET /api/connectors/lunch_money/secret/access_token). The GET only ever echoes
// has_access_token; a blank token field on save leaves the saved one unchanged. This form
// owns the config fields (token / enable); fetching transactions is now Lucius's job via a
// template (see the connector page's Automate button) — the unified /api/connectors PATCH owns the
// card-header enable toggle. Save lives in the modal footer — see ./formSave.

interface LunchMoneyStatus {
  configured: boolean
  enabled: boolean
  has_access_token: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface LunchMoneyForm {
  access_token: string
}

/** The seed for `load()` AND the baseline the footer's Save compares against. `enabled`
 *  is gone from this form (the top-level Pause/Resume button owns it now), which leaves
 *  `access_token` — and that never round-trips (write-only) — so the form has no server
 *  value left to seed itself from; it is always this same blank shape. */
function lunchMoneyFormOf(): LunchMoneyForm {
  return {
    access_token: '',
  }
}

export function LunchMoneySection() {
  const [cfg, setCfg] = useState<LunchMoneyStatus | null>(null)
  const [form, setForm] = useState<LunchMoneyForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/lunch-money/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: LunchMoneyStatus) => {
        setCfg(c)
        setForm(lunchMoneyFormOf())
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = { enabled: cfg.configured ? cfg.enabled : true }
      if (form.access_token.trim()) body.access_token = form.access_token.trim()
      const r = await authFetch('/api/lunch-money/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the access token.')
        return false
      }
      setForm((f) => f && { ...f, access_token: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setToken = useCallback((v: string) => setForm((f) => f && { ...f, access_token: v }), [])
  const token = useSecretReveal({
    saved: !!cfg?.has_access_token,
    value: form?.access_token ?? '',
    onChange: setToken,
    reveal: useCallback(() => revealConnectorSecret('lunch_money', 'access_token'), []),
  })

  const tokenValid = !form || !!form.access_token.trim() || !!cfg?.has_access_token
  const tokenTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && tokenValid,
    values: form && { ...form, access_token: unrevealed(form.access_token, token) },
    baseline: cfg ? lunchMoneyFormOf() : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Lunch Money connector status.
      </Text>
    )
  }
  if (!cfg || !form) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }

  const set = <K extends keyof LunchMoneyForm>(k: K, v: LunchMoneyForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        If you already track spending in{' '}
        <a href="https://lunchmoney.app" target="_blank" rel="noreferrer">
          Lunch Money
        </a>
        , paste a personal access token from its Settings → Developers page. Fetching and booking
        transactions is Lucius's job — automate it with a template below. Pulls transactions only —
        Lunch Money's own manually-tracked assets are not imported.
      </Text>

      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}

      <ConnectorStatusLine lastSyncedAt={cfg.last_synced_at} lastStatus={cfg.last_status} />
      {cfg.last_error && (
        <Text size="xs" tone="bad">
          Last error: {cfg.last_error}
        </Text>
      )}

      <FormField
        label="Access token"
        htmlFor="conn-lm-token"
        required
        help={token.caption || 'Your Lunch Money personal access token (Settings → Developers).'}
        error={
          token.error ||
          (tokenTouched.touched && !tokenValid ? 'An access token is required.' : undefined)
        }
      >
        <PasswordField
          id="conn-lm-token"
          value={form.access_token}
          placeholder={cfg.has_access_token ? '••••••••' : 'a1b2c3d4e5f6…'}
          onChange={(e) => set('access_token', e.target.value)}
          onBlur={tokenTouched.onBlur}
          {...token.fieldProps}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      <ConnectorAccountsList provider="lunch_money" providerLabel="Lunch Money" />
    </Stack>
  )
}
