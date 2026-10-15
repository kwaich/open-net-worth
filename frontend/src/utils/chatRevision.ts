// A conversation's server change stamp (GET /api/sessions/{id}/revision, also returned with
// every transcript fetch). Remembering the stamp of the last fetch lets a returning client ask
// "anything new?" for a few bytes instead of re-reading the transcript — and show "Catching
// up…" only when the answer is yes.

import { authFetch } from '../api/client'

const synced = new Map<string, string>()

/** Called with the stamp that came back WITH a transcript fetch. '' (unknown) is not kept. */
export function recordRevision(convId: string, revision: unknown): void {
  if (typeof revision === 'string' && revision) synced.set(convId, revision)
}

/** A fetched transcript was thrown away unshown, so its stamp claims a sync that never
 *  happened: drop it, and the next return re-reads instead of trusting "unchanged". */
export function forgetRevision(convId: string): void {
  synced.delete(convId)
}

/** The stamp of this browser's last transcript fetch for the conversation, if any. */
export function syncedRevision(convId: string): string | undefined {
  return synced.get(convId)
}

/** The server's current stamp, or null when it can't be had (offline, an older server). */
export async function fetchRevision(convId: string): Promise<string | null> {
  try {
    const res = await authFetch(`/api/sessions/${convId}/revision`)
    if (!res.ok) return null
    const data = (await res.json()) as { revision?: unknown }
    return typeof data.revision === 'string' && data.revision ? data.revision : null
  } catch {
    return null
  }
}
