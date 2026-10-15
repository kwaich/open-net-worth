import { useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Badge, Cluster, Stack, Switch, Text } from '../../ui'
import { timeAgo } from '../../../utils/format'

// Yahoo Finance connector config — reuses the SAME /api/market-data/config + /status
// endpoints the retired "Data" settings panel used (MarketDataPanel.tsx), just hosted
// inside the connector detail page now, mirroring how every other section here reuses its
// existing per-provider REST plane rather than growing a new one (see ConnectorConfig.tsx's
// own module docstring).
//
// Yahoo Finance is not `pausable` — equities/ETFs always resolve through it
// first, and supported major crypto priced in USD does too when the toggle below is on;
// nothing in services/prices.py's fetch chain gates on an "enabled" flag, so there is no
// Pause/Connect action to offer (ConnectorDetail suppresses the primary action for
// `alwaysOn` connectors). The ONE real setting here — "use Yahoo first for supported major
// crypto" — is a write-on-toggle switch, not a Save-gated form field: it holds no secret,
// so there is nothing an accidental click could leak, and every other switch-shaped
// setting in this app writes immediately rather than waiting on a separate Save.

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

export function YahooFinanceSection() {
  const [cfg, setCfg] = useState<MarketDataConfig | null>(null)
  const [status, setStatus] = useState<MarketDataStatus | null>(null)
  const [error, setError] = useState(false)
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
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  async function setYahooFirst(next: boolean) {
    if (!cfg) return
    const previous = cfg
    setCfg({ ...cfg, yahoo_primary_for_major_crypto: next })
    setSaving(true)
    setSaveErr(null)
    try {
      const r = await authFetch(CONFIG_URL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ yahoo_primary_for_major_crypto: next }),
      })
      if (!r.ok) throw new Error('save failed')
      setCfg((await r.json()) as MarketDataConfig)
    } catch {
      setCfg(previous)
      setSaveErr('Could not save — try again.')
    } finally {
      setSaving(false)
    }
  }

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load Yahoo Finance&apos;s settings.
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

  const yahooHealth = status
    ? health(status.yahoo)
    : { label: 'Not checked yet', tone: 'neutral' as const }

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Every equity and ETF quote comes from Yahoo Finance — no account or API key needed.
        Supported major crypto pairs priced in USD use it first too, before falling back to
        CoinGecko.
      </Text>

      <Cluster justify="between" gap="--space-4" wrap={false}>
        <Text size="xs" tone="faint">
          {status?.yahoo.last_ok_at
            ? `Last successful ${relativeTime(status.yahoo.last_ok_at)}`
            : 'No successful call recorded yet.'}
        </Text>
        <Badge tone={yahooHealth.tone}>{yahooHealth.label}</Badge>
      </Cluster>

      <Cluster justify="between" gap="--space-4" align="center" wrap={false}>
        <Stack gap="--space-1">
          <Text size="sm">Use Yahoo Finance first for supported major crypto</Text>
          <Text size="xs" tone="faint">
            Recommended. Turn this off only to temporarily restore CoinGecko-first chart and quote
            requests — identities and saved charts do not change.
          </Text>
        </Stack>
        <Switch
          checked={cfg.yahoo_primary_for_major_crypto}
          disabled={saving}
          aria-label="Use Yahoo Finance first for supported major crypto"
          onChange={(e) => void setYahooFirst(e.target.checked)}
        />
      </Cluster>

      {!cfg.yahoo_primary_for_major_crypto && (
        <Text size="xs" tone="warn">
          CoinGecko is used first for major crypto right now, not Yahoo Finance.
        </Text>
      )}

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
