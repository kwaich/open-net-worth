import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'

/** Whether Settings should offer the Terminal: always on a Private Cloud server, and on a
 *  self-hosted app only when its owner has switched the terminal on. */
export function useTerminalAvailable(gatewayMode: boolean): boolean {
  const [available, setAvailable] = useState(false)
  useEffect(() => {
    if (gatewayMode) return
    let cancelled = false
    authFetch('/api/terminal/status')
      .then((r) => (r.ok ? r.json() : null))
      .then((s: { available?: boolean } | null) => {
        if (!cancelled) setAvailable(Boolean(s?.available))
      })
      .catch(() => {})
    return () => {
      cancelled = true
    }
  }, [gatewayMode])
  return gatewayMode || available
}
