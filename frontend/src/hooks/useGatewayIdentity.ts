import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'

export interface GatewayIdentity {
  email: string | null
  portal_url: string | null
}

/** Who the Private Cloud gateway says is signed in, and where its portal lives. Null outside
 *  gateway mode (and until loaded). Deliberately not in the persisted query cache or the cached
 *  auth status: an email address has no business in browser storage. */
export function useGatewayIdentity(enabled: boolean): GatewayIdentity | null {
  const [identity, setIdentity] = useState<GatewayIdentity | null>(null)
  useEffect(() => {
    if (!enabled) return
    let cancelled = false
    authFetch('/api/auth/status')
      .then((r) => r.json())
      .then((s: { gateway?: Partial<GatewayIdentity> }) => {
        if (cancelled || !s.gateway) return
        setIdentity({ email: s.gateway.email ?? null, portal_url: s.gateway.portal_url ?? null })
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [enabled])
  return enabled ? identity : null
}
