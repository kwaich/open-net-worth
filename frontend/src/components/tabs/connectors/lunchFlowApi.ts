// Wire calls for the Lunch Flow connector — status/config/disconnect over
// `/api/lunch-flow/*`, plus the OAuth begin/poll pair (`beginLunchFlowOAuth`/
// `pollLunchFlowOAuth`) that `lunchFlowOAuth.ts`'s popup+poll controller drives. Mirrors
// `mcpApi.ts`'s own OAuth wire-call shape (RFC `2026-08-14-mcp-oauth` §4.2), scoped to this
// connector's single config instead of a per-server registry.

import { authFetch } from '../../../api/client'

export interface LunchFlowConnectionAccount {
  lunch_flow_account_id: string
  name: string
  onw_account_id: string
}

export interface LunchFlowConnection {
  connection_id: number
  institution_name: string
  accounts: LunchFlowConnectionAccount[]
}

export interface LunchFlowStatus {
  configured: boolean
  enabled: boolean
  has_credentials: boolean
  client_id: string
  email: string
  has_link: boolean
  user_id: string
  last_synced_at: string | null
  last_status: string
  last_error: string | null
  last_caution: string | null
  connections: LunchFlowConnection[]
}

export async function fetchLunchFlowStatus(): Promise<LunchFlowStatus | null> {
  const res = await authFetch('/api/lunch-flow/status')
  if (!res.ok) return null
  return (await res.json()) as LunchFlowStatus
}

export interface LunchFlowConfigUpdate {
  client_id?: string
  client_secret?: string
  email?: string
  enabled?: boolean
}

export async function updateLunchFlowConfig(
  body: LunchFlowConfigUpdate,
): Promise<{ ok: true; status: LunchFlowStatus } | { ok: false; error: string }> {
  const res = await authFetch('/api/lunch-flow/config', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const d = await res.json().catch(() => ({}) as { detail?: string })
    return { ok: false, error: d.detail || 'Could not save the credentials.' }
  }
  return { ok: true, status: (await res.json()) as LunchFlowStatus }
}

export async function disconnectLunchFlowConnection(
  connectionId: number,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const res = await authFetch(`/api/lunch-flow/connections/${connectionId}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const d = await res.json().catch(() => ({}) as { detail?: string })
    return { ok: false, error: d.detail || 'Could not disconnect.' }
  }
  return { ok: true }
}

// ── OAuth sign-in (bank link) ───────────────────────────────────────────────────────────
//
// `POST /api/lunch-flow/oauth/begin` returns an `authorize_url` to open in a popup, or an
// honest `{ok: false, error}` — always a 200, matching the MCP OAuth flow's "the request
// succeeded, the vendor is what didn't answer" convention. `GET /api/lunch-flow/oauth/{flow_id}`
// polls that flow's outcome and NEVER returns a token.

export interface LunchFlowOAuthBeginResult {
  ok: boolean
  authorize_url?: string
  flow_id?: string
  expires_in?: number
  error?: string
}

async function errorDetail(res: Response, fallback: string): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null
  const detail = body?.detail
  return typeof detail === 'string' && detail ? detail : `${fallback} (${res.status})`
}

export async function beginLunchFlowOAuth(): Promise<LunchFlowOAuthBeginResult> {
  try {
    const res = await authFetch('/api/lunch-flow/oauth/begin', { method: 'POST' })
    if (!res.ok) {
      return { ok: false, error: await errorDetail(res, 'Could not start the sign-in') }
    }
    return (await res.json()) as LunchFlowOAuthBeginResult
  } catch {
    return { ok: false, error: 'Could not start the sign-in — the request failed.' }
  }
}

export interface LunchFlowOAuthPollResult {
  status: 'pending' | 'ok' | 'error'
  error: string | null
}

export async function pollLunchFlowOAuth(flowId: string): Promise<LunchFlowOAuthPollResult> {
  try {
    const res = await authFetch(`/api/lunch-flow/oauth/${encodeURIComponent(flowId)}`, {
      cache: 'no-store',
    })
    if (!res.ok) return { status: 'error', error: 'Could not check the sign-in status.' }
    return (await res.json()) as LunchFlowOAuthPollResult
  } catch {
    return { status: 'error', error: 'Could not check the sign-in status — the request failed.' }
  }
}
