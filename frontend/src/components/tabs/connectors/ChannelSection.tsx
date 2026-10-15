import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import {
  Stack,
  Cluster,
  Text,
  Button,
  FormField,
  TextField,
  PasswordField,
  useSecretReveal,
} from '../../ui'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { WEBHOOK_PRESETS, webhookPreset } from './presets'
import { revealConnectorSecret } from './secrets'

// Webhook config form over /api/channels/webhook (per-provider plane). The webhook URL and
// its secret are BOTH credentials (a Slack/Discord incoming-webhook URL is itself a bearer
// token): the GET echoes only `has_url` / `has_secret` and the URL's host, a blank field
// leaves the saved value unchanged, and each saved value is read back through its field's own
// eye (GET /api/connectors/webhook/secret/{url|secret}). Email's alert channel is part of the
// Email connector (`EmailUnifiedSection`). Save lives in the modal footer — see ./formSave.

interface WebhookChannel {
  name: 'webhook'
  enabled: boolean
  secret_header_name: string
  has_url: boolean
  /** The URL's host — the part that cannot authenticate; shown in place of the saved URL. */
  url_display: string
  has_secret: boolean
  configured: boolean
}

async function fetchWebhook(): Promise<WebhookChannel | null> {
  const r = await authFetch('/api/channels')
  if (!r.ok) return null
  const data = (await r.json()) as { channels: WebhookChannel[] }
  return data.channels.find((c) => c.name === 'webhook') ?? null
}

function TestRow({ path }: { path: string }) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  async function test() {
    setBusy(true)
    setResult(null)
    try {
      const r = await authFetch(path, { method: 'POST' })
      if (!r.ok) {
        setResult({ ok: false, text: 'Nothing to test yet — save the settings first.' })
        return
      }
      const d = await r.json()
      const err = d?.result?.error
      setResult(err ? { ok: false, text: String(err) } : { ok: true, text: 'Test sent.' })
    } catch {
      setResult({ ok: false, text: 'Could not reach the server.' })
    } finally {
      setBusy(false)
    }
  }
  return (
    <Cluster gap="--space-3" align="center">
      <Button size="sm" variant="secondary" loading={busy} onClick={test}>
        Send test
      </Button>
      {result && (
        <Text size="xs" tone={result.ok ? 'good' : 'bad'}>
          {result.text}
        </Text>
      )}
    </Cluster>
  )
}

// ── Webhook (send) ───────────────────────────────────────────────────────────

interface WebhookForm {
  url: string
  secret_header_name: string
  secret: string
}

function webhookFormOf(c: WebhookChannel): WebhookForm {
  return { url: '', secret_header_name: c.secret_header_name, secret: '' }
}

export function WebhookSection() {
  const [cfg, setCfg] = useState<WebhookChannel | null>(null)
  // Slack/Discord are pure URL-shape presets — they only adjust the URL placeholder
  // + the where-to-find-it hint; the user pastes their own webhook URL.
  const [presetKey, setPresetKey] = useState<string>('')
  const [form, setForm] = useState<WebhookForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    fetchWebhook()
      .then((c) => {
        if (!c) {
          setError(true)
          return
        }
        setCfg(c)
        setForm(webhookFormOf(c))
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  const setUrl = useCallback((v: string) => setForm((f) => f && { ...f, url: v }), [])
  const url = useSecretReveal({
    saved: !!cfg?.has_url,
    value: form?.url ?? '',
    onChange: setUrl,
    reveal: useCallback(() => revealConnectorSecret('webhook', 'url'), []),
  })
  const setSecret = useCallback((v: string) => setForm((f) => f && { ...f, secret: v }), [])
  const secret = useSecretReveal({
    saved: !!cfg?.has_secret,
    value: form?.secret ?? '',
    onChange: setSecret,
    reveal: useCallback(() => revealConnectorSecret('webhook', 'secret'), []),
  })

  const typedUrl = form ? unrevealed(form.url, url).trim() : ''
  const urlValid = !form || (typedUrl ? /^https?:\/\/\S+$/.test(typedUrl) : !!cfg?.has_url)
  const urlTouched = useTouched()

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        secret_header_name: form.secret_header_name.trim() || 'X-Webhook-Secret',
        enabled: cfg.configured ? cfg.enabled : true,
      }
      if (typedUrl) body.url = typedUrl
      const typedSecret = unrevealed(form.secret, secret)
      if (typedSecret) body.secret = typedSecret
      const r = await authFetch('/api/channels/webhook', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the webhook channel.')
        return false
      }
      setForm((f) => f && { ...f, url: '', secret: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  useConnectorSave({
    ready: !!form && !!cfg && urlValid,
    values: form && { ...form, url: typedUrl, secret: unrevealed(form.secret, secret) },
    baseline: cfg ? webhookFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the webhook settings.
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

  const set = <K extends keyof WebhookForm>(k: K, v: WebhookForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  const preset = webhookPreset(presetKey)

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Send notifications to a URL — a Slack or Discord incoming webhook, or your own endpoint.
      </Text>
      <Cluster gap="--space-2" align="center" className="conn-preset-row">
        <Text size="xs" tone="faint">
          Preset:
        </Text>
        {WEBHOOK_PRESETS.map((pr) => (
          <button
            key={pr.key}
            type="button"
            className={`preset-chip${presetKey === pr.key ? ' preset-chip--active' : ''}`}
            onClick={() => setPresetKey((k) => (k === pr.key ? '' : pr.key))}
          >
            {pr.label}
          </button>
        ))}
      </Cluster>
      {preset && (
        <Text size="xs" tone="faint">
          {preset.note}
        </Text>
      )}
      <FormField
        label="URL"
        htmlFor="conn-wh-url"
        required
        help={url.caption || undefined}
        error={
          url.error ||
          (urlTouched.touched && !urlValid
            ? typedUrl
              ? 'Enter a valid http:// or https:// URL.'
              : 'A webhook URL is required.'
            : undefined)
        }
      >
        <PasswordField
          id="conn-wh-url"
          autoComplete="off"
          value={form.url}
          placeholder={
            cfg.has_url
              ? `Saved — ${cfg.url_display}`
              : preset?.url_placeholder || 'https://hooks.example.com/…'
          }
          onChange={(e) => set('url', e.target.value)}
          onBlur={urlTouched.onBlur}
          {...url.fieldProps}
        />
      </FormField>
      <FormField label="Secret header name" htmlFor="conn-wh-header">
        <TextField
          id="conn-wh-header"
          value={form.secret_header_name}
          placeholder="X-Webhook-Secret"
          onChange={(e) => set('secret_header_name', e.target.value)}
        />
      </FormField>
      <FormField
        label="Secret"
        htmlFor="conn-wh-secret"
        help={secret.caption || undefined}
        error={secret.error}
      >
        <PasswordField
          id="conn-wh-secret"
          autoComplete="off"
          value={form.secret}
          placeholder={cfg.has_secret ? '••••••••' : ''}
          onChange={(e) => set('secret', e.target.value)}
          {...secret.fieldProps}
        />
      </FormField>
      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
      <TestRow path="/api/channels/webhook/test" />
    </Stack>
  )
}
