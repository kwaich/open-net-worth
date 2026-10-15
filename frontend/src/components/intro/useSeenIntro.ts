import { useCallback, useEffect, useState } from 'react'
import { PROFILE_KEY } from '../../hooks/serverState/keys'
import { fetchJson } from '../../hooks/serverState/fetchJson'
import { fetchCachedQuery, getCachedQueryData, setCachedQueryData } from '../../state/queryCache'

type Profile = { seen_intros?: string[] }

const seenIn = (p: Profile | null | undefined, key: string) => (p?.seen_intros ?? []).includes(key)

/** Whether a feature intro has been shown, remembered on the server (`profile.seen_intros`)
 *  so it never repeats on another device. Until the profile is known the intro counts as
 *  seen: an unreadable profile shows nothing rather than an intro that may be a repeat. */
export function useSeenIntro(key: string): { unseen: boolean; markSeen: () => void } {
  const [seen, setSeen] = useState(() => {
    const cached = getCachedQueryData<Profile | null>(PROFILE_KEY)
    return cached ? seenIn(cached, key) : true
  })

  useEffect(() => {
    let alive = true
    fetchCachedQuery<Profile | null>(PROFILE_KEY, () => fetchJson<Profile>(PROFILE_KEY))
      .then((p) => {
        if (alive && p) setSeen(seenIn(p, key))
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [key])

  const markSeen = useCallback(() => {
    setSeen(true)
    // Read the list fresh: the cached copy may predate a key another device just added.
    void fetchJson<Profile>(PROFILE_KEY)
      .then((fresh) => {
        const list = [...new Set([...(fresh.seen_intros ?? []), key])]
        return fetchJson<{ profile: Profile }>(PROFILE_KEY, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ seen_intros: list }),
        })
      })
      .then((saved) => setCachedQueryData<Profile | null>(PROFILE_KEY, saved.profile))
      .catch(() => {})
  }, [key])

  return { unseen: !seen, markSeen }
}
