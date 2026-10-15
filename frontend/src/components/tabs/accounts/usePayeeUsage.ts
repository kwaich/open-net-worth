// Usage numbers (how many live transactions use each payee, and when it was last used) are a
// Manage-Payees concern only. The picker list (`GET /api/payees`) no longer carries them, so
// the editor asks for them itself — on open, and again whenever the list it was handed changes
// (a merge, delete or reassign moves the counts).
import { useEffect, useMemo, useState } from 'react'
import { fetchJson } from '../../../hooks/serverState/fetchJson'
import type { ApiPayee, Payee } from './types'

type Usage = { txnCount: number; lastUsed: string | null }

export function usePayeeUsage(payees: Payee[]): Payee[] {
  const [usage, setUsage] = useState<ReadonlyMap<string, Usage>>(() => new Map())
  useEffect(() => {
    let live = true
    fetchJson<ApiPayee[] | { payees?: ApiPayee[] }>('/api/payees?usage=true')
      .then((raw) => {
        if (!live) return
        const list = Array.isArray(raw) ? raw : (raw.payees ?? [])
        setUsage(
          new Map(
            list.map((p) => [p.id, { txnCount: p.txn_count ?? 0, lastUsed: p.last_used ?? null }]),
          ),
        )
      })
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [payees])
  return useMemo(() => payees.map((p) => ({ ...p, ...usage.get(p.id) })), [payees, usage])
}
