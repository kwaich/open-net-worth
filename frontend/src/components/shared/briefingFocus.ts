// The daily briefing's standing instruction (`briefing_preferences.custom_focus`) — what the
// server tells Lucius every time it writes the briefing. The Overview widget's editor writes it
// here and asks the briefing to regenerate; it never sends a chat message.
import { authFetch } from '../../api/client'
import { PROFILE_KEY, fetchJson } from '../../hooks/serverState'
import { invalidateQueryCache } from '../../state/queryCache'

/** Fired after the instruction is saved; a mounted briefing regenerates with it. */
export const BRIEFING_REGENERATE_EVENT = 'onw:briefing-regenerate'

type Profile = { briefing_preferences?: Record<string, unknown> }

export async function readBriefingFocus(): Promise<string> {
  const profile = await fetchJson<Profile>(PROFILE_KEY)
  return String(profile.briefing_preferences?.custom_focus ?? '')
}

export async function saveBriefingFocus(focus: string): Promise<void> {
  // Merge into the stored preferences: the section toggles live in the same object.
  const profile = await fetchJson<Profile>(PROFILE_KEY)
  const prefs = { ...(profile.briefing_preferences ?? {}), custom_focus: focus.trim() }
  const res = await authFetch(PROFILE_KEY, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ briefing_preferences: prefs }),
  })
  if (!res.ok) throw new Error(`${PROFILE_KEY} → HTTP ${res.status}`)
  invalidateQueryCache(PROFILE_KEY)
  window.dispatchEvent(new Event(BRIEFING_REGENERATE_EVENT))
}
