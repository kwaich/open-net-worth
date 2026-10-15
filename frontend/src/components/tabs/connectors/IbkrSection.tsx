import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, PasswordField, TextField, useSecretReveal } from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// IBKR (Interactive Brokers) Flex Web Service connector config over /api/ibkr/*.
// The Flex token is a secret — GET only echoes `has_token`; a blank token field leaves the
// saved value unchanged, and the saved token is read back through the eye (GET
// /api/connectors/ibkr/secret/flex_token). The query ID (not a secret) is shown/editable.
// S1 is read-only by construction: the Flex token cannot place orders. Fetching activity is
// now Lucius's job via a template (see the connector page's Automate button).

interface IbkrStatus {
  configured: boolean
  has_token: boolean
  flex_query_id: string
  enabled: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
}

interface IbkrForm {
  flex_token: string
  flex_query_id: string
}

/** The form as the server last returned it — both the seed for `load()` and the baseline
 *  the footer's Save compares against, so the two can never drift apart. */
function ibkrFormOf(c: IbkrStatus): IbkrForm {
  return {
    flex_token: '',
    flex_query_id: c.flex_query_id || '',
  }
}

export function IbkrSection() {
  const [cfg, setCfg] = useState<IbkrStatus | null>(null)
  const [form, setForm] = useState<IbkrForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/ibkr/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: IbkrStatus) => {
        setCfg(c)
        setForm(ibkrFormOf(c))
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
      const token = form.flex_token.trim()
      const qid = form.flex_query_id.trim()
      if (token) body.flex_token = token
      if (qid) body.flex_query_id = qid

      const r = await authFetch('/api/ibkr/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the settings.')
        return false
      }
      setForm((f) => f && { ...f, flex_token: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setToken = useCallback((v: string) => setForm((f) => f && { ...f, flex_token: v }), [])
  const token = useSecretReveal({
    saved: !!cfg?.has_token,
    value: form?.flex_token ?? '',
    onChange: setToken,
    reveal: useCallback(() => revealConnectorSecret('ibkr', 'flex_token'), []),
  })

  const tokenValid = !form || !!form.flex_token.trim() || !!cfg?.has_token
  const flexQueryIdValid = !form || !!form.flex_query_id.trim()
  const tokenTouched = useTouched()
  const flexQueryIdTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && tokenValid && flexQueryIdValid,
    values: form && { ...form, flex_token: unrevealed(form.flex_token, token) },
    baseline: cfg ? ibkrFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the IBKR connector status.
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

  const set = <K extends keyof IbkrForm>(k: K, v: IbkrForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        <a href="https://www.interactivebrokers.com/" target="_blank" rel="noreferrer">
          Interactive Brokers
        </a>{' '}
        exports your account as a Flex Query. Set one up in Account Management (Reports → Flex
        Queries → Activity), set <strong>Format</strong> to <strong>XML</strong>, include{' '}
        <strong>OpenPositions</strong>, <strong>Trades</strong> and{' '}
        <strong>CashTransactions</strong>, then paste its token and ID below.
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
        label="Flex token"
        htmlFor="conn-ibkr-token"
        required
        help={
          token.caption ||
          'Your IBKR Flex Web Service token (from Account Management → Flex Queries → Generate Token).'
        }
        error={
          token.error ||
          (tokenTouched.touched && !tokenValid ? 'A Flex token is required.' : undefined)
        }
      >
        <PasswordField
          id="conn-ibkr-token"
          value={form.flex_token}
          placeholder={cfg.has_token ? '••••••••' : 'Paste Flex token'}
          onChange={(e) => set('flex_token', e.target.value)}
          onBlur={tokenTouched.onBlur}
          {...token.fieldProps}
        />
      </FormField>

      <FormField
        label="Flex query ID"
        htmlFor="conn-ibkr-queryid"
        required
        help="The numeric ID of your saved Flex Query (visible in the IBKR Flex Query list)."
        error={
          flexQueryIdTouched.touched && !flexQueryIdValid
            ? 'A Flex query ID is required.'
            : undefined
        }
      >
        <TextField
          id="conn-ibkr-queryid"
          value={form.flex_query_id}
          placeholder="e.g. 123456"
          onChange={(e) => set('flex_query_id', e.target.value)}
          onBlur={flexQueryIdTouched.onBlur}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      <ConnectorAccountsList provider="ibkr" providerLabel="Interactive Brokers" />

      <Text size="xs" tone="faint">
        The token can only export data — it cannot place or cancel orders.
      </Text>
    </Stack>
  )
}
