import { useEffect, useRef } from 'react'
import { authFetch } from '../api/client'
import { setCachedQueryData } from '../state/queryCache'
import type { ProfileData } from './serverState'
import { PROFILE_KEY } from './serverState'

/** The browser's IANA zone, or null where `Intl` cannot say. */
export function browserTimeZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null
  } catch {
    return null
  }
}

/**
 * Report the browser's time zone to the server ONCE per session, only while the book has none.
 * The server is the arbiter: a zone already stored (picked in Settings, or captured earlier) is
 * never overwritten, so this is safe to fire whenever the profile loads without one.
 */
export function useTimezoneCapture(profile: ProfileData | undefined): void {
  const attempted = useRef(false)
  useEffect(() => {
    if (!profile || profile.timezone || attempted.current) return
    const zone = browserTimeZone()
    if (!zone) return
    attempted.current = true
    void authFetch(`${PROFILE_KEY}/timezone/detect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timezone: zone }),
    })
      .then(async (res) => {
        if (!res.ok) return
        const state = (await res.json()) as {
          timezone: string | null
          timezone_source: ProfileData['timezone_source']
        }
        setCachedQueryData<ProfileData>(PROFILE_KEY, (prev) => ({
          ...(prev ?? profile),
          timezone: state.timezone,
          timezone_source: state.timezone_source,
        }))
      })
      .catch(() => {
        attempted.current = false
      })
  }, [profile])
}
