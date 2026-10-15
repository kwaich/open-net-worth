import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Badge,
  Button,
  Cluster,
  FormField,
  PasswordField,
  SegmentedControl,
  Stack,
  Text,
  useSecretReveal,
} from '../../ui'
import { timeAgo } from '../../../utils/format'
import { unrevealed, useConnectorSave } from './formSave'
import { revealConnectorSecret } from './secrets'

// CoinGecko connector config over the SAME /api/market-data/config + /status endpoints the
// retired "Data" settings panel used (MarketDataPanel.tsx) — see YahooFinanceSection's own
// module comment for why this reuses rather than rewrites that plane.
//
// CoinGecko is not `pausable` either (the automatic fallback in the crypto price
// chain — Yahoo, then CoinGecko, then recent saved data — nothing gates it on an enable
// flag), but UNLIKE Yahoo Finance it holds a real credential: an optional API key. That key
// previously had no reveal — pasted once, "never shown again" per its own old help text.
// This section fixes that with the same `useSecretReveal` + eye every other connector's
// secret field uses (see TelegramSection's bot-token field for the identical pattern),
// reading the saved value back through the generic
// `GET /api/connectors/coingecko/secret/api_key` reveal endpoint.
//
// A key is either Demo or Pro tier (CoinGecko's own product split), each validated against a
// DIFFERENT host and header by the backend (`services/coingecko_client.py`) — Demo-only
// support was a real gap, since a paid Pro key sent through the Demo host/header simply
// fails. The tier is a closed choice (a segmented control, never free text): auto-detecting
// it from the key's shape would be unreliable, and a wrong guess would silently break every
// price lookup rather than fail loudly.

interface MarketDataConfig {
  has_coingecko_key: boolean
  coingecko_api_key_tier: 'demo' | 'pro'
  yahoo_primary_for_major_crypto: boolean
}

interface ProviderHealth {
  last_ok_at: string | null
  failure_streak: number
  cooldown_until?: string | null
}

interface MarketDataStatus extends ProviderHealth {
  cooldown_until: string | null
  calls_last_minute: number
  yahoo: ProviderHealth
}

const CONFIG_URL = '/api/market-data/config'
const STATUS_URL = '/api/market-data/status'

const TIER_OPTIONS = [
  { value: 'demo' as const, label: 'Demo' },
  { value: 'pro' as const, label: 'Pro' },
]

function relativeTime(iso: string): string {
  const at = new Date(iso).getTime()
  return Number.isFinite(at) && at > Date.now() ? 'just now' : timeAgo(iso)
}

function health(status: ProviderHealth): {
  label: string
  tone: 'pos' | 'warn' | 'neg' | 'neutral'
} {
  if (status.cooldown_until) return { label: 'Cooling down', tone: 'warn' }
  if (status.failure_streak > 0) {
    return status.last_ok_at
      ? { label: 'Recovering', tone: 'warn' }
      : { label: 'Unavailable', tone: 'neg' }
  }
  if (!status.last_ok_at) return { label: 'Not checked yet', tone: 'neutral' }
  return { label: 'Available', tone: 'pos' }
}

export function CoinGeckoSection() {
  const [cfg, setCfg] = useState<MarketDataConfig | null>(null)
  const [status, setStatus] = useState<MarketDataStatus | null>(null)
  const [error, setError] = useState(false)
  const [key, setKey] = useState('')
  const [tier, setTier] = useState<'demo' | 'pro'>('demo')
  // Set by the "Clear saved key" action below — the field being blank because nothing was
  // typed must NOT clear the saved key (a save triggered by, say, a tier-only change), so a
  // real "wipe it" intent needs its own signal distinct from an untouched blank box. See
  // `market_data_config_store.MarketDataConfigStore.update`'s PATCH-shaped contract: an absent
  // or blank key keeps, an explicit `null` clears.
  const [clearRequested, setClearRequested] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    Promise.all([
      authFetch(CONFIG_URL).then((r) => (r.ok ? r.json() : Promise.reject())),
      authFetch(STATUS_URL).then((r) => (r.ok ? r.json() : Promise.reject())),
    ])
      .then(([c, s]: [MarketDataConfig, MarketDataStatus]) => {
        setCfg(c)
        setStatus(s)
        setKey('')
        setClearRequested(false)
        setTier(c.coingecko_api_key_tier)
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  function handleKeyChange(value: string) {
    setKey(value)
    // Typing overrides a pending clear — the operator changed their mind and is replacing
    // the key instead of removing it.
    setClearRequested(false)
  }

  function handleClearKey() {
    if (clearRequested) {
      setClearRequested(false)
      return
    }
    setKey('')
    setClearRequested(true)
  }

  async function save(): Promise<boolean> {
    setSaving(true)
    setSaveErr(null)
    try {
      const body: { coingecko_api_key?: string | null; coingecko_api_key_tier: string } = {
        coingecko_api_key_tier: tier,
      }
      if (clearRequested) {
        body.coingecko_api_key = null
      } else if (key.trim()) {
        body.coingecko_api_key = key.trim()
      }
      const r = await authFetch(CONFIG_URL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        setSaveErr('Could not save the API key.')
        return false
      }
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const keyReveal = useSecretReveal({
    saved: !!cfg?.has_coingecko_key,
    value: key,
    onChange: handleKeyChange,
    reveal: useCallback(() => revealConnectorSecret('coingecko', 'api_key'), []),
  })

  useConnectorSave({
    ready: !!cfg,
    values: { key: unrevealed(key, keyReveal), tier, clearRequested },
    baseline: { key: '', tier: cfg?.coingecko_api_key_tier ?? 'demo', clearRequested: false },
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load CoinGecko&apos;s settings.
      </Text>
    )
  }
  if (!cfg) {
    return (
      <Text size="sm" tone="faint">
        Loading…
      </Text>
    )
  }

  const cgHealth = status ? health(status) : { label: 'Not checked yet', tone: 'neutral' as const }
  // Our own local self-throttle, not a claim about the account's real CoinGecko entitlement
  // (a Pro plan's actual limit varies by plan and CoinGecko does not publish one universal
  // number) — see `coingecko_client.py`'s own comment. Demo/keyless quotas are real, fixed
  // figures worth showing; Pro is not, so the calls counter alone (with no "/ N" ratio) is
  // the honest way to show it without implying a number nobody promised.
  const quotaLabel =
    tier === 'pro' && cfg.has_coingecko_key
      ? status
        ? `${status.calls_last_minute} calls this minute`
        : 'Provider status unavailable.'
      : status
        ? `${status.calls_last_minute} / ${cfg.has_coingecko_key ? 25 : 4} calls this minute`
        : 'Provider status unavailable.'
  const lastOk =
    status?.last_ok_at && status.last_ok_at
      ? ` • Last successful ${relativeTime(status.last_ok_at)}`
      : ''

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Used whenever Yahoo Finance can&apos;t price a crypto asset, plus crypto search and facts.
        Works with no key on a small shared free quota; add a Demo or Pro API key (from
        CoinGecko&apos;s own site) for a higher one — Pro plans set their own limits with CoinGecko
        directly.
      </Text>

      <Cluster justify="between" gap="--space-4" wrap={false}>
        <Text size="xs" tone="faint">
          {quotaLabel}
          {lastOk}
        </Text>
        <Badge tone={cgHealth.tone}>{cgHealth.label}</Badge>
      </Cluster>

      <FormField label="Key tier" htmlFor="conn-coingecko-tier">
        <SegmentedControl
          ariaLabel="CoinGecko key tier"
          value={tier}
          onChange={setTier}
          options={TIER_OPTIONS}
        />
      </FormField>

      <FormField
        label="API key"
        htmlFor="conn-coingecko-key"
        help={
          keyReveal.caption ||
          (clearRequested
            ? 'The saved key will be removed when you save.'
            : cfg.has_coingecko_key
              ? 'A key is saved — leave blank to keep it. Changing the tier above applies to the saved key too.'
              : 'Optional. Raises the quota above the small shared free one — Demo and Pro keys use different limits.')
        }
        error={keyReveal.error}
      >
        <PasswordField
          id="conn-coingecko-key"
          value={key}
          placeholder={cfg.has_coingecko_key ? '••••••••' : 'Paste your Demo or Pro API key'}
          onChange={(e) => handleKeyChange(e.target.value)}
          {...keyReveal.fieldProps}
        />
      </FormField>

      {cfg.has_coingecko_key && (
        <Cluster gap="--space-3" align="center">
          <Button size="sm" variant="ghost" disabled={saving} onClick={handleClearKey}>
            {clearRequested ? 'Undo clear' : 'Clear saved key'}
          </Button>
        </Cluster>
      )}

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
