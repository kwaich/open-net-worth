import { authFetch } from './client'

// The TYPE and the three labels belong to the CONTROL (ui/PermissionModeControl), which the
// Automation editor and the composer both render — one vocabulary, one place. This module is
// only the transport.
import type { PermissionMode } from '../components/ui/PermissionModeControl'

export type { PermissionMode }

const PERMISSION_MODES: readonly PermissionMode[] = ['manual', 'auto', 'skip']

export function normalizeMode(value: unknown): PermissionMode {
  // Fails toward ASKING, mirroring core.conversation_approval.normalize_mode. An unreadable
  // or unexpected value must never render as `skip`.
  return PERMISSION_MODES.includes(value as PermissionMode) ? (value as PermissionMode) : 'manual'
}

export interface PermissionModeState {
  mode: PermissionMode
  /** False for an Automation run — the Automation's own mode governs, and it is changed on
   *  the Automation, not here. The control renders disabled rather than lying about what is
   *  in force. */
  editable: boolean
}

export async function getPermissionMode(sessionId: string): Promise<PermissionModeState> {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(sessionId)}/permission-mode`)
  if (!res.ok) throw new Error(`permission-mode read failed (${res.status})`)
  const body = await res.json()
  return { mode: normalizeMode(body?.mode), editable: body?.editable !== false }
}

export async function setPermissionMode(
  sessionId: string,
  mode: PermissionMode,
): Promise<PermissionMode> {
  const res = await authFetch(`/api/sessions/${encodeURIComponent(sessionId)}/permission-mode`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  })
  if (!res.ok) throw new Error(`permission-mode write failed (${res.status})`)
  return normalizeMode((await res.json())?.mode)
}
