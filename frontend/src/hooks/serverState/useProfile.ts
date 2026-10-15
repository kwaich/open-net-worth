import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { PROFILE_KEY } from './keys'

export interface ProfileData {
  base_currency?: string
  target_allocation?: Record<string, number>
  risk_concentration_guideline?: number
  risk_stress_drop_pct?: number
  import_review_default?: string
  is_demo?: boolean
  name?: string
  /** "What should Lucius call you?" (Settings → Profile) — the greeting's preferred name,
   *  distinct from `name` (the full legal/display name). Empty/unset falls back to `name`. */
  nickname?: string | null
  /** The owner's IANA time zone, and whether it was captured from the browser (`auto`) or picked
   *  in Settings (`user`). Unset = the server's own zone. */
  timezone?: string | null
  timezone_source?: 'auto' | 'user' | null
  [key: string]: unknown
}

const fetchProfile = () => fetchJson<ProfileData>(PROFILE_KEY)

/**
 * The shared `/api/profile` read (12 independent call sites before consolidation).
 * Refreshes on the `profile` data_changed domain.
 */
export function useProfile(): QueryCacheResult<ProfileData> {
  const version = useDataVersion('profile')
  return useQueryCache<ProfileData>(PROFILE_KEY, fetchProfile, { staleTime: 60_000, version })
}
