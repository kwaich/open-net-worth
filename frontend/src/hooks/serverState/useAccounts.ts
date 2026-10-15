import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { ACCOUNTS_KEY } from './keys'

/**
 * Structural minimum every consumer relies on. Deliberately permissive + generic rather than
 * importing `components/tabs/accounts/types.ts`'s `Account`: this hook is shared by 18 call
 * sites with differing needs (some want balances, some only id/name), and a shared hook should
 * not drag one tab's view model across the app. Callers that want the full type pass it in:
 * `useAccounts<Account>()`.
 */
export interface AccountLike {
  id: string
  name?: string
  currency?: string
  archived?: boolean
  [key: string]: unknown
}

const fetchAccounts = () => fetchJson<AccountLike[]>(ACCOUNTS_KEY)

/**
 * The shared `/api/accounts` read — the single most duplicated request in the app
 * (18 independent call sites before consolidation).
 *
 * NOTE: this is the DEFAULT (non-archived) list. A caller needing `?include_archived=true`
 * must use its own key; that variant is intentionally not folded in here, because the two
 * responses differ and silently serving the wrong one is a correctness bug, not a perf win.
 */
export function useAccounts<T extends AccountLike = AccountLike>(): QueryCacheResult<T[]> {
  const version = useDataVersion('accounts')
  return useQueryCache<T[]>(ACCOUNTS_KEY, fetchAccounts as () => Promise<T[]>, {
    staleTime: 30_000,
    version,
  })
}
