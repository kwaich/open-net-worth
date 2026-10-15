import { authFetch } from './client'
import type { OptionsBook } from '../types/portfolio'

export async function fetchOptionsBook(): Promise<OptionsBook> {
  const r = await authFetch('/api/options')
  if (!r.ok) throw new Error(`GET /api/options failed: ${r.status}`)
  return r.json()
}

/** POST /api/options returned 409: an identical open contract exists (double-book guard).
 * Re-submit with allow_duplicate=true only after the user confirms it is a real second fill. */
export class DuplicateOptionError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DuplicateOptionError'
  }
}

async function detailOf(r: Response, fallback: string): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: string }
    return body.detail || fallback
  } catch {
    return fallback
  }
}

export async function openOption(
  body: Record<string, unknown>,
): Promise<{ ok: boolean; contract_id: string }> {
  const r = await authFetch('/api/options', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!r.ok) {
    const detail = await detailOf(r, `HTTP ${r.status}`)
    if (r.status === 409) throw new DuplicateOptionError(detail)
    throw new Error(detail)
  }
  return r.json()
}

export interface CloseOptionBody {
  closed_at?: string
  close_premium_per_share?: number
  premium_account_id?: string
}

export async function closeOption(id: string, body: CloseOptionBody): Promise<{ ok: boolean }> {
  const r = await authFetch(`/api/options/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'close', ...body }),
  })
  if (!r.ok) throw new Error(await detailOf(r, `HTTP ${r.status}`))
  return r.json()
}

export async function deleteOption(id: string, force = false): Promise<{ ok: boolean }> {
  const r = await authFetch(`/api/options/${encodeURIComponent(id)}${force ? '?force=true' : ''}`, {
    method: 'DELETE',
  })
  if (!r.ok) throw new Error(await detailOf(r, `HTTP ${r.status}`))
  return r.json()
}
