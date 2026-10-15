import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, PasswordField } from '../../ui'
import { useConnectorSave, useTouched } from './formSave'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// SimpleFIN connector config over /api/simplefin/* (per-provider plane), mirroring
// YodleeSection. The access URL is a WRITE-ONLY, MACHINE-MINTED credential — it is what
// claiming a one-time setup token returns, never a value the operator types and keeps
// re-using — so unlike a typed secret (Plaid's `secret`, Yodlee's `secret`/`password`) it
// has no reveal eye: it mirrors Plaid's own `access_token`, which `connector_secrets.py`
// deliberately excludes from its revealable-fields allowlist for the identical reason. The
// GET only echoes `has_access_url`; a blank setup-token field leaves the saved access URL
// unchanged. This form owns the config fields (setup token / enable); fetching transactions
// is now Lucius's job via a template (see the connector page's Automate button) — the unified
// /api/connectors PATCH owns the card-header enable toggle. Save lives in the modal
// footer — see ./formSave.

interface SimpleFinStatus {
  configured: boolean
  enabled: boolean
  has_access_url: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface SimpleFinForm {
  setup_token: string
}

/** The seed for `load()` AND the baseline the footer's Save compares against. `enabled`
 *  is gone from this form (the top-level Pause/Resume button owns it now), which leaves
 *  `setup_token` — and that never round-trips (write-only, single-use) — so the form has
 *  no server value left to seed itself from; it is always this same blank shape. */
function simplefinFormOf(): SimpleFinForm {
  return {
    setup_token: '',
  }
}

export function SimpleFinSection() {
  const [cfg, setCfg] = useState<SimpleFinStatus | null>(null)
  const [form, setForm] = useState<SimpleFinForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/simplefin/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: SimpleFinStatus) => {
        setCfg(c)
        setForm(simplefinFormOf())
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
      if (form.setup_token.trim()) body.setup_token = form.setup_token.trim()
      const r = await authFetch('/api/simplefin/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save. The setup token may be invalid or already used.')
        return false
      }
      setForm((f) => f && { ...f, setup_token: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setupTokenValid = !form || !!form.setup_token.trim() || !!cfg?.has_access_url
  const setupTokenTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && setupTokenValid,
    values: form,
    baseline: cfg ? simplefinFormOf() : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the SimpleFIN connector status.
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

  const set = <K extends keyof SimpleFinForm>(k: K, v: SimpleFinForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Aggregate your bank &amp; card balances and transactions automatically via{' '}
        <a href="https://www.simplefin.org/" target="_blank" rel="noreferrer">
          SimpleFIN
        </a>{' '}
        (US / Canada, ~$15/yr — no OAuth). Create a SimpleFIN access at your provider, then paste
        the one-time <strong>setup token</strong> below. Fetching and booking transactions is
        Lucius's job — automate it with a template below.
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
        label="Setup token"
        htmlFor="conn-sf-token"
        required
        help={
          cfg.has_access_url
            ? 'An access token is saved. Leave blank to keep it; paste a new setup token to re-link.'
            : 'A one-time base64 token from your SimpleFIN provider (e.g. SimpleFIN Bridge).'
        }
        error={
          setupTokenTouched.touched && !setupTokenValid
            ? 'A SimpleFIN setup token is required.'
            : undefined
        }
      >
        <PasswordField
          id="conn-sf-token"
          value={form.setup_token}
          placeholder={cfg.has_access_url ? '••••••••' : 'aHR0cHM6Ly9iZWFyZXI6…'}
          onChange={(e) => set('setup_token', e.target.value)}
          onBlur={setupTokenTouched.onBlur}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      <ConnectorAccountsList provider="simplefin" providerLabel="SimpleFIN" />
    </Stack>
  )
}
