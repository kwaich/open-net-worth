import { invalidateQueryCache, useCachedFetchState } from '../state/queryCache'
import { fetchVault, type VaultListing } from '../api/vault'
import { useDataVersion } from './useDataVersion'

export const VAULT_QUERY_KEY = 'vault:listing'

// Anything Lucius does that can add or drop a Vault item: keeping a file (vault), a receipt
// linked or deleted (receipts), a statement imported (ledger/uploads), an upload deleted — and
// any change to an asset's files (assets): added, removed, renamed or given a note there.
const VAULT_DOMAINS = ['vault', 'uploads', 'receipts', 'ledger', 'artifacts', 'accounts', 'assets']

/** The whole Vault listing, shared by the panel and every artifact card's Save button — one
 *  request, however many cards are on screen. */
export function useVault(options: { staleTime?: number } = {}) {
  const version = useDataVersion(VAULT_DOMAINS)
  return useCachedFetchState<VaultListing>(VAULT_QUERY_KEY, fetchVault, {
    staleTime: options.staleTime,
    version,
  })
}

export function refreshVault(): void {
  invalidateQueryCache(VAULT_QUERY_KEY)
}
