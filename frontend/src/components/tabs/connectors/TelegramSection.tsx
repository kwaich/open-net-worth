import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../../../api/client'
import { Stack, Text, FormField, TextField, PasswordField, useSecretReveal } from '../../ui'
import { AccessPolicyField, PendingApprovals, type AccessPolicy } from './ChatAccessPolicy'
import { unrevealed, useConnectorSave, useTouched } from './formSave'
import { revealConnectorSecret } from './secrets'

// Telegram connector config over /api/telegram/config (per-provider plane), mirroring the
// SMTP channel section. A blank token field leaves the saved one unchanged; the
// GET echoes only a `has_bot_token` flag, and the saved token is read back through the eye
// (GET /api/connectors/telegram/secret/bot_token). The detail page's top-level Pause/Resume
// button is the ONLY place `enabled` is switched now; this form owns the config fields
// (token / allowed chats) and PASSES THROUGH whatever `enabled` already was —
// never hardcodes it — so saving a config edit can't have the side effect of silently
// resuming a bot the operator deliberately paused (security review, 2026-08-17).
//
// The intro used to explain why Telegram needs no "Operator trust" toggle "unlike Slack and
// Discord" — both of which were REMOVED in 2026-08, so it contrasted this connector with two
// that no longer exist, using a control name the user has never seen.
//
// NO webhook URL field (removed 2026-08-22): it called Telegram's set_webhook but nothing in
// this codebase ever received the pushed updates — configuring one silently broke the bot
// (stopped polling, delivered nowhere). Long-polling only; see backend/api/integrations.py.

interface TelegramConfig {
  configured: boolean
  enabled: boolean
  allowed_chat_ids: string[]
  access_policy: AccessPolicy
  has_bot_token: boolean
}

interface TelegramForm {
  bot_token: string
  allowed_chat_ids: string
  access_policy: AccessPolicy
}

// The only two policies still offered as a PICK (see ChatAccessPolicy.tsx) — 'open'/'disabled'
// are still real stored values (Remove deliberately writes 'disabled' as a security measure;
// see TelegramConfigStore.reset()), just no longer selectable here.
const PICKABLE_POLICIES: AccessPolicy[] = ['pairing', 'allowlist']

/** The form as the server last returned it — both the seed for `load()` and the baseline
 *  the footer's Save compares against, so the two can never drift apart.
 *
 *  A stored policy outside `PICKABLE_POLICIES` (2026-08-22, caught live: Remove → re-add)
 *  falls back to 'pairing', never passed through raw. A raw 'disabled' bound to a <select>
 *  with no matching <option> renders the browser's default (the FIRST option, "Pairing") —
 *  so the dropdown visibly showed Pairing while the real form state was still 'disabled', and
 *  hitting Save without ever touching the dropdown silently re-persisted 'disabled': a
 *  freshly re-added, fully "Connected" bot that answered nobody, with nothing in the UI
 *  saying why. */
function formOf(c: TelegramConfig): TelegramForm {
  return {
    bot_token: '',
    allowed_chat_ids: (c.allowed_chat_ids || []).join(', '),
    access_policy: PICKABLE_POLICIES.includes(c.access_policy) ? c.access_policy : 'pairing',
  }
}

export function TelegramSection() {
  const [cfg, setCfg] = useState<TelegramConfig | null>(null)
  const [form, setForm] = useState<TelegramForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  function load() {
    setError(false)
    authFetch('/api/telegram/config')
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((c: TelegramConfig) => {
        setCfg(c)
        setForm(formOf(c))
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
        allowed_chat_ids: form.allowed_chat_ids,
        access_policy: form.access_policy,
      }
      if (form.bot_token.trim()) body.bot_token = form.bot_token.trim()
      const r = await authFetch('/api/telegram/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok) {
        const d = await r.json().catch(() => ({}))
        setSaveErr(d.detail || 'Could not save the Telegram configuration.')
        return false
      }
      setForm((f) => f && { ...f, bot_token: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  const setToken = useCallback((v: string) => setForm((f) => f && { ...f, bot_token: v }), [])
  const token = useSecretReveal({
    saved: !!cfg?.has_bot_token,
    value: form?.bot_token ?? '',
    onChange: setToken,
    reveal: useCallback(() => revealConnectorSecret('telegram', 'bot_token'), []),
  })

  const tokenValid = !form || !!form.bot_token.trim() || !!cfg?.has_bot_token
  const tokenTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && tokenValid,
    values: form && { ...form, bot_token: unrevealed(form.bot_token, token) },
    baseline: cfg ? formOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Telegram connector config.
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

  const set = <K extends keyof TelegramForm>(k: K, v: TelegramForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Ask questions, send photos, and approve changes from your own Telegram bot — the same things
        Lucius can do in the app. Create a bot with{' '}
        <a href="https://t.me/BotFather" target="_blank" rel="noreferrer">
          @BotFather
        </a>{' '}
        and paste its token below.
      </Text>
      {cfg.configured && !cfg.enabled && (
        <Text size="xs" tone="warn">
          Currently paused — saving here won&apos;t resume it. Use Resume on the connector card to
          turn it back on.
        </Text>
      )}

      <FormField
        label="Bot token"
        htmlFor="conn-tg-token"
        required
        help={token.caption || 'From @BotFather, e.g. 123456:ABC-DEF…'}
        error={
          token.error ||
          (tokenTouched.touched && !tokenValid ? 'A bot token is required.' : undefined)
        }
      >
        <PasswordField
          id="conn-tg-token"
          value={form.bot_token}
          placeholder={cfg.has_bot_token ? '••••••••' : '123456:ABC-DEF…'}
          onChange={(e) => set('bot_token', e.target.value)}
          onBlur={tokenTouched.onBlur}
          {...token.fieldProps}
        />
      </FormField>

      {/* "Who can chat" BEFORE "Allowed chat IDs" — the field below only matters for TYPING an
          id in under Allowlist (Pairing fills it in automatically via approvals), so asking
          someone to fill it in before they've even picked a mode was backwards (owner review,
          2026-08-22). Still shown under Pairing though (2026-08-22, same-day follow-up): it's
          the SAME list pairing approvals write into — hiding it entirely made an approval
          disappear with no visible confirmation or way to review/revoke who's gotten in. */}
      <AccessPolicyField
        idPrefix="conn-tg"
        value={form.access_policy}
        onChange={(v) => set('access_policy', v)}
      />

      <PendingApprovals
        connector="telegram"
        active={form.access_policy === 'pairing'}
        onApproved={load}
      />

      <FormField
        label={form.access_policy === 'pairing' ? 'People who can chat' : 'Allowed chat IDs'}
        htmlFor="conn-tg-allowed"
        help={
          form.access_policy === 'pairing'
            ? 'Fills in as you approve. Or paste a chat ID (send /id to the bot).'
            : 'Send /id to the bot to get your chat ID. Blank means no one can reach Lucius.'
        }
      >
        <TextField
          id="conn-tg-allowed"
          value={form.allowed_chat_ids}
          placeholder="123456789, -100987654321"
          onChange={(e) => set('allowed_chat_ids', e.target.value)}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}
    </Stack>
  )
}
