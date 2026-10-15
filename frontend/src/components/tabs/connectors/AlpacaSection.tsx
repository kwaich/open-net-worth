import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Stack,
  Cluster,
  Text,
  FormField,
  PasswordField,
  Select,
  Badge,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'
import { fCur } from '../accounts/format'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// Alpaca brokerage connector config over /api/alpaca/* (per-provider plane), modelled on
// PlaidSection. The API key id + secret are read back through their own eyes (GET
// /api/connectors/alpaca/secret/api_key_id, .../secret_key) — the GET echoes only `has_keys`;
// blank fields leave the saved creds unchanged. This panel owns the READ config (creds /
// paper-vs-live / enable); fetching positions is now Lucius's job via a template (see
// the connector page's Automate button). This connector is READ-ONLY: the trading sub-panel and its
// `trading_enabled` kill switch were REMOVED 2026-07-19 — see
// docs/adr/2026-07-19-remove-alpaca-trading-half.md. `environment` stays because it selects
// the Alpaca API base URL the read fetch reads from. Save lives in the modal footer — see
// ./formSave.

interface AlpacaStatus {
  configured: boolean
  enabled: boolean
  environment: string
  has_keys: boolean
  last_synced_at: string | null
  last_status: string
  last_error: string | null
  cash: string | number | null
  buying_power: string | number | null
}

interface AlpacaForm {
  api_key_id: string
  secret_key: string
  environment: string
}

/** The server's values as a form — the seed for `load()` AND the baseline the footer's
 *  Save compares against, so the two can never drift apart. */
function alpacaFormOf(c: AlpacaStatus): AlpacaForm {
  return {
    api_key_id: '',
    secret_key: '',
    environment: c.environment || 'paper',
  }
}

const ENV_OPTIONS = [
  { value: 'paper', label: 'Paper account' },
  { value: 'live', label: 'Live account' },
]

export function AlpacaSection() {
  const [cfg, setCfg] = useState<AlpacaStatus | null>(null)
  const [form, setForm] = useState<AlpacaForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/alpaca/status')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: AlpacaStatus) => {
        setCfg(c)
        setForm(alpacaFormOf(c))
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        enabled: cfg.configured ? cfg.enabled : true,
        environment: form.environment,
      }
      // Both keys are sent together, or neither (a half-set credential is rejected server-side).
      if (form.api_key_id.trim() && form.secret_key.trim()) {
        body.api_key_id = form.api_key_id.trim()
        body.secret_key = form.secret_key.trim()
      } else if (form.api_key_id.trim() || form.secret_key.trim()) {
        setSaveErr('Provide BOTH the API key id and secret, or leave both empty.')
        return false
      }
      const r = await authFetch('/api/alpaca/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the credentials.')
        return false
      }
      setForm((f) => f && { ...f, api_key_id: '', secret_key: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setKeyId = useCallback((v: string) => setForm((f) => f && { ...f, api_key_id: v }), [])
  const keyId = useSecretReveal({
    saved: !!cfg?.has_keys,
    value: form?.api_key_id ?? '',
    onChange: setKeyId,
    reveal: useCallback(() => revealConnectorSecret('alpaca', 'api_key_id'), []),
  })

  const setSecretKey = useCallback((v: string) => setForm((f) => f && { ...f, secret_key: v }), [])
  const secretKey = useSecretReveal({
    saved: !!cfg?.has_keys,
    value: form?.secret_key ?? '',
    onChange: setSecretKey,
    reveal: useCallback(() => revealConnectorSecret('alpaca', 'secret_key'), []),
  })

  // Both keys are required together — typed fresh OR already-saved (server-side) counts as
  // valid, same "already saved counts as valid" shape as the app password in
  // EmailUnifiedSection. A half-set pair (one filled, one blank) is never valid.
  const keyIdFilled = !!form?.api_key_id.trim()
  const secretFilled = !!form?.secret_key.trim()
  const keysHalfSet = keyIdFilled !== secretFilled
  const keysValid = !form || (!keysHalfSet && (keyIdFilled || !!cfg?.has_keys))
  const keyIdTouched = useTouched()
  const secretKeyTouched = useTouched()
  // The half-set case ("enter the other one too") is real-time feedback that only appears
  // once the user has actually typed into ONE of the pair, so it needs no touched-gate of
  // its own. The neither-filled case would otherwise read as an error before anyone has
  // typed anything — gated behind `touched` so a fresh connector's blank pair starts quiet.
  const pairError = (
    mineFilled: boolean,
    otherFilled: boolean,
    label: string,
    touched: boolean,
  ): string | undefined => {
    if (mineFilled) return undefined
    if (otherFilled) return `Enter the ${label} too — both keys are required together.`
    return touched && !cfg?.has_keys ? `The ${label} is required.` : undefined
  }

  useConnectorSave({
    ready: !!form && !!cfg && keysValid,
    values: form && {
      ...form,
      api_key_id: unrevealed(form.api_key_id, keyId),
      secret_key: unrevealed(form.secret_key, secretKey),
    },
    baseline: cfg ? alpacaFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Alpaca connector status.
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

  const set = <K extends keyof AlpacaForm>(k: K, v: AlpacaForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  const isLive = cfg.environment === 'live'

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Add your{' '}
        <a href="https://alpaca.markets/" target="_blank" rel="noreferrer">
          Alpaca
        </a>{' '}
        API key pair, then pick paper or live to match the account they belong to. Connecting Alpaca
        creates a dedicated Alpaca account here — retire any manual copy you were keeping.
      </Text>

      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}

      {cfg.configured && (
        <Cluster gap="--space-3" align="center" wrap>
          <Badge tone={isLive ? 'neg' : 'muted'}>{isLive ? 'LIVE' : 'Paper'}</Badge>
        </Cluster>
      )}
      <ConnectorStatusLine lastSyncedAt={cfg.last_synced_at} lastStatus={cfg.last_status} />
      {(cfg.cash != null || cfg.buying_power != null) && (
        <Cluster gap="--space-3" align="center" wrap>
          {cfg.cash != null && (
            <Text size="xs" tone="faint">
              Cash {fCur(Number(cfg.cash), 'USD')}
            </Text>
          )}
          {cfg.buying_power != null && (
            <Text size="xs" tone="faint">
              Buying power {fCur(Number(cfg.buying_power), 'USD')}
            </Text>
          )}
        </Cluster>
      )}
      {cfg.last_error && (
        <Text size="xs" tone="bad">
          Last error: {cfg.last_error}
        </Text>
      )}

      <FormField
        label="API key id"
        htmlFor="conn-alpaca-keyid"
        required
        help={keyId.caption || 'Your Alpaca APCA-API-KEY-ID (from the Alpaca dashboard).'}
        error={
          keyId.error || pairError(keyIdFilled, secretFilled, 'API key id', keyIdTouched.touched)
        }
      >
        <PasswordField
          id="conn-alpaca-keyid"
          value={form.api_key_id}
          placeholder={cfg.has_keys ? '••••••••' : 'PK…'}
          onChange={(e) => set('api_key_id', e.target.value)}
          onBlur={keyIdTouched.onBlur}
          {...keyId.fieldProps}
        />
      </FormField>

      <FormField
        label="Secret key"
        htmlFor="conn-alpaca-secret"
        required
        help={secretKey.caption || 'Your Alpaca APCA-API-SECRET-KEY.'}
        error={
          secretKey.error ||
          pairError(secretFilled, keyIdFilled, 'secret key', secretKeyTouched.touched)
        }
      >
        <PasswordField
          id="conn-alpaca-secret"
          value={form.secret_key}
          placeholder={cfg.has_keys ? '••••••••' : 'secret'}
          onChange={(e) => set('secret_key', e.target.value)}
          onBlur={secretKeyTouched.onBlur}
          {...secretKey.fieldProps}
        />
      </FormField>

      <FormField label="Environment" htmlFor="conn-alpaca-env">
        <Select
          id="conn-alpaca-env"
          value={form.environment}
          options={ENV_OPTIONS}
          onChange={(e) => set('environment', e.target.value)}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      <ConnectorAccountsList provider="alpaca" providerLabel="Alpaca" />
    </Stack>
  )
}
