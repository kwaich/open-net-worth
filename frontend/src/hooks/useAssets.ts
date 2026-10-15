import { useCallback } from 'react'
import type {
  AssetCategory,
  AssetLoan,
  AssetsPayload,
  AssetsSummary,
  CashAccountOption,
  OwnedAsset,
} from '../types/assets'
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'
import { useDataVersion } from './useDataVersion'

export interface AssetsState {
  data: AssetsPayload | undefined
  assets: OwnedAsset[]
  categories: AssetCategory[]
  loans: AssetLoan[]
  cashAccounts: CashAccountOption[]
  summary: AssetsSummary | undefined
  baseCurrency: string | undefined
  loading: boolean
  error: string | null
  refresh: () => void
}

/** Fetches /api/assets — the register, its categories/loans/cash-account pickers, and the
 *  server-computed summary strip. Mirrors useCommitments.ts; staleTime 0 like every other
 *  money-bearing read in the app (every write in tabs/assets/api.ts announces its domains,
 *  which bumps `version` below; this just guards against a stale mount). */
export function useAssets(): AssetsState {
  // One line per tab (useDataVersion.ts's own doc comment) — without this, a write from
  // Lucius (every asset tool is tagged mutates=("assets",)) never refetches an already-mounted
  // Assets tab; only a fresh mount does, since staleTime is 0. The payload is built from more
  // than assets: the loan picker (and each loan's `held_by`), the cash accounts and the summary
  // come from accounts, the ledger and positions — so this watches the same set as
  // useNetWorth, or a Lucius create_account would leave the loan list stale.
  const version = useDataVersion(['assets', 'accounts', 'ledger', 'portfolio'])
  const query = useQueryCache<AssetsPayload>(
    '/api/assets',
    async () => {
      const response = await authFetch('/api/assets')
      if (!response.ok) throw new Error(`Failed to load assets (${response.status})`)
      return response.json()
    },
    { staleTime: 0, version },
  )
  const refresh = useCallback(() => {
    void query.refresh()
  }, [query])
  return {
    data: query.data,
    assets: query.data?.assets ?? [],
    categories: query.data?.categories ?? [],
    loans: query.data?.loans ?? [],
    cashAccounts: query.data?.cash_accounts ?? [],
    summary: query.data?.summary,
    baseCurrency: query.data?.base_currency,
    loading: query.initialLoading,
    error: query.error instanceof Error ? query.error.message : null,
    refresh,
  }
}
