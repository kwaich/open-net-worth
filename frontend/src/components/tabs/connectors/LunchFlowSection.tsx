import { useCallback, useEffect, useRef, useState } from 'react'
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
import { revealConnectorSecret } from './secrets'
import {
  fetchLunchFlowStatus,
  updateLunchFlowConfig,
  disconnectLunchFlowConnection,
  type LunchFlowStatus,
} from './lunchFlowApi'
import { startLunchFlowOAuth, type LunchFlowOAuthHandle } from './lunchFlowOAuth'
import { ConnectorStatusLine } from './ConnectorStatusLine'
import { ConnectorAccountsList } from './ConnectorAccountsList'

// Lunch Flow connector config over /api/lunch-flow/* — mirrors PlaidSection's shape more than
// a paste-a-token form (SimpleFinSection/LunchMoneySection), since Lunch Flow is an OAuth
// PLATFORM: the owner's own developer client_id/secret pair (from lunchflow.app's own
// dashboard) plus a "Connect bank" button that opens Lunch Flow's hosted login+bank-link page
// in a popup and polls for the outcome (the same popup+poll shape the MCP OAuth sign-in flow
// established, reused here as its own connector-specific instance — see the connector's own
// ADR). The client secret is read back through its own eye (GET
// /api/connectors/lunch_flow/secret/client_secret); the OAuth tokens stay write-only and are
// never revealed to the browser at all, not even behind an eye. This form owns the config
// fields (creds / email / enable) + Connect/Disconnect; fetching transactions is now Lucius's
// job via a template (see the connector page's Automate button) — the unified /api/connectors PATCH owns the
// card-header enable toggle. Save lives in the modal footer — see ./formSave.

interface LunchFlowForm {
  client_id: string
  client_secret: string
  email: string
}

/** The server's values as a form — the seed for `load()` AND the baseline the footer's Save
 *  compares against, so the two can never drift apart. */
function lunchFlowFormOf(c: LunchFlowStatus): LunchFlowForm {
  return {
    client_id: c.client_id || '',
    client_secret: '',
    email: c.email || '',
  }
}

export function LunchFlowSection() {
  const [cfg, setCfg] = useState<LunchFlowStatus | null>(null)
  const [form, setForm] = useState<LunchFlowForm | null>(null)
  const [error, setError] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveErr, setSaveErr] = useState<string | null>(null)

  // Bank-link flow state.
  const [connecting, setConnecting] = useState(false)
  const [connectErr, setConnectErr] = useState<string | null>(null)
  const oauthHandleRef = useRef<LunchFlowOAuthHandle | null>(null)

  const [disconnectingId, setDisconnectingId] = useState<number | null>(null)
  const [disconnectErr, setDisconnectErr] = useState<string | null>(null)

  function load() {
    setError(false)
    fetchLunchFlowStatus()
      .then((c) => {
        if (!c) throw new Error()
        setCfg(c)
        setForm(lunchFlowFormOf(c))
      })
      .catch(() => setError(true))
  }

  useEffect(load, [])

  // A running sign-in must not keep polling after the form navigates away.
  useEffect(() => () => oauthHandleRef.current?.cancel(), [])

  async function save(): Promise<boolean> {
    if (!form || !cfg) return false
    setSaving(true)
    setSaveErr(null)
    try {
      const body: Record<string, unknown> = {
        enabled: cfg.configured ? cfg.enabled : true,
        client_id: form.client_id.trim(),
        email: form.email.trim(),
      }
      if (form.client_secret.trim()) body.client_secret = form.client_secret.trim()
      const result = await updateLunchFlowConfig(body)
      if (!result.ok) {
        setSaveErr(result.error)
        return false
      }
      setForm((f) => f && { ...f, client_secret: '' })
      load()
      return true
    } catch {
      setSaveErr('Could not save — could not reach the server.')
      return false
    } finally {
      setSaving(false)
    }
  }

  function connectBank() {
    oauthHandleRef.current?.cancel()
    setConnecting(true)
    setConnectErr(null)
    oauthHandleRef.current = startLunchFlowOAuth((outcome) => {
      oauthHandleRef.current = null
      setConnecting(false)
      if (!outcome.ok) {
        setConnectErr(outcome.error || 'Sign-in did not complete.')
        return
      }
      load()
    })
  }

  function cancelConnect() {
    oauthHandleRef.current?.cancel()
    oauthHandleRef.current = null
    setConnecting(false)
  }

  async function disconnect(connectionId: number, institutionName: string) {
    // One click here fires DELETE at Lunch Flow itself — an irreversible external action
    // (the vendor unlinks the institution; imported transactions stay in the ledger either
    // way). Every other irreversible control in this app confirms first (e.g.
    // StatementsSection's delete), so this does too.
    const label = institutionName || 'this bank'
    if (
      !window.confirm(
        `Disconnect ${label}? This unlinks it at Lunch Flow — you'll need to reconnect it ` +
          `there to sync again. Transactions already imported stay in your ledger.`,
      )
    ) {
      return
    }
    setDisconnectingId(connectionId)
    setDisconnectErr(null)
    try {
      const result = await disconnectLunchFlowConnection(connectionId)
      if (!result.ok) {
        setDisconnectErr(result.error)
        return
      }
      load()
    } finally {
      setDisconnectingId(null)
    }
  }

  const setClientSecret = useCallback(
    (v: string) => setForm((f) => f && { ...f, client_secret: v }),
    [],
  )
  const secret = useSecretReveal({
    saved: !!cfg?.has_credentials,
    value: form?.client_secret ?? '',
    onChange: setClientSecret,
    reveal: useCallback(() => revealConnectorSecret('lunch_flow', 'client_secret'), []),
  })

  const clientSecretValid = !form || !!form.client_secret.trim() || !!cfg?.has_credentials
  const clientSecretTouched = useTouched()

  useConnectorSave({
    ready: !!form && !!cfg && clientSecretValid,
    values: form && { ...form, client_secret: unrevealed(form.client_secret, secret) },
    baseline: cfg ? lunchFlowFormOf(cfg) : form,
    saving,
    error: saveErr,
    save,
  })

  if (error) {
    return (
      <Text size="sm" tone="bad">
        Could not load the Lunch Flow connector status.
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

  const set = <K extends keyof LunchFlowForm>(k: K, v: LunchFlowForm[K]) =>
    setForm((f) => f && { ...f, [k]: v })

  return (
    <Stack gap="--space-4">
      <Text size="sm" tone="faint">
        Add your{' '}
        <a href="https://lunchflow.app/developer" target="_blank" rel="noreferrer">
          Lunch Flow
        </a>{' '}
        developer client ID and secret, then connect a bank — you&apos;ll sign in at Lunch
        Flow&apos;s own hosted page in a new window and link your bank there. Fetching and booking
        transactions is Lucius's job — automate it with a template below.
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
      {/* Always rendered regardless of last_status — a caution about the amount sign must
          stay visible even on a technically-"ok" fetch, or it goes unseen (security review). */}
      {cfg.last_caution && (
        <Text size="xs" tone="warn">
          Caution: {cfg.last_caution}
        </Text>
      )}

      <FormField
        label="Client ID"
        htmlFor="conn-lunchflow-id"
        required
        help="Your Lunch Flow client_id (from the Lunch Flow developer dashboard)."
      >
        <TextField
          id="conn-lunchflow-id"
          value={form.client_id}
          placeholder="lf_client_..."
          onChange={(e) => set('client_id', e.target.value)}
        />
      </FormField>

      <FormField
        label="Client secret"
        htmlFor="conn-lunchflow-secret"
        required
        help={secret.caption || 'Your Lunch Flow client_secret for that same app.'}
        error={
          secret.error ||
          (clientSecretTouched.touched && !clientSecretValid
            ? 'A client secret is required.'
            : undefined)
        }
      >
        <PasswordField
          id="conn-lunchflow-secret"
          value={form.client_secret}
          placeholder={cfg.has_credentials ? '••••••••' : 'client secret'}
          onChange={(e) => set('client_secret', e.target.value)}
          onBlur={clientSecretTouched.onBlur}
          {...secret.fieldProps}
        />
      </FormField>

      <FormField
        label="Email"
        htmlFor="conn-lunchflow-email"
        help="Optional — pre-fills your email on Lunch Flow's sign-in page."
      >
        <TextField
          id="conn-lunchflow-email"
          type="email"
          value={form.email}
          placeholder="you@example.com"
          onChange={(e) => set('email', e.target.value)}
        />
      </FormField>

      {saveErr && (
        <Text size="xs" tone="bad">
          {saveErr}
        </Text>
      )}

      {/* ── Bank link flow (Lunch Flow hosted sign-in, popup + poll) ── */}
      <Stack gap="--space-3">
        <Text size="sm" tone="ink">
          {cfg.has_link ? 'Linked bank' : 'Connect a bank'}
        </Text>
        {connecting ? (
          <Cluster gap="--space-3" align="center">
            <Text size="xs" tone="faint">
              Waiting for sign-in…
            </Text>
            <Button size="sm" variant="secondary" onClick={cancelConnect}>
              Cancel
            </Button>
          </Cluster>
        ) : (
          <Cluster gap="--space-3" align="center" wrap>
            <Button size="sm" disabled={!cfg.has_credentials} onClick={connectBank}>
              {cfg.has_link ? 'Reconnect bank' : 'Connect bank'}
            </Button>
            <Text size="xs" tone="faint">
              Opens Lunch Flow's hosted sign-in in a new window.
            </Text>
          </Cluster>
        )}

        {connectErr && (
          <Text size="xs" tone="bad">
            {connectErr}
          </Text>
        )}
      </Stack>

      {/* ── Linked institutions (one row per Lunch Flow connection, with a Disconnect) ── */}
      {cfg.connections.length > 0 && (
        <Stack gap="--space-2">
          <Text size="sm" tone="ink">
            Linked institutions
          </Text>
          {cfg.connections.map((conn) => (
            <Cluster key={conn.connection_id} gap="--space-3" align="center" wrap justify="between">
              <Text size="xs" tone="faint">
                {conn.institution_name || `Connection ${conn.connection_id}`} •{' '}
                {conn.accounts.length} account{conn.accounts.length !== 1 ? 's' : ''}
              </Text>
              <Button
                size="sm"
                variant="secondary"
                loading={disconnectingId === conn.connection_id}
                onClick={() => disconnect(conn.connection_id, conn.institution_name)}
              >
                Disconnect
              </Button>
            </Cluster>
          ))}
          {disconnectErr && (
            <Text size="xs" tone="bad">
              {disconnectErr}
            </Text>
          )}
        </Stack>
      )}

      <ConnectorAccountsList provider="lunch_flow" providerLabel="Lunch Flow" />
    </Stack>
  )
}
