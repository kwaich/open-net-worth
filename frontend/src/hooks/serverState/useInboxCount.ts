import { useDataVersion } from '../useDataVersion'
import { type QueryCacheResult, useQueryCache } from '../../state/queryCache'
import { fetchJson } from './fetchJson'
import { INBOX_COUNT_KEY } from './keys'

export interface InboxCount {
  unread_count?: number
}

const fetchInboxCount = () => fetchJson<InboxCount>(INBOX_COUNT_KEY)

/**
 * Unread-inbox badge count. Previously re-fetched on every `pathname` change — now a shared
 * 30s-stale entry, so navigating between tabs reuses it instead of issuing a fresh request
 * per navigation.
 */
export function useInboxCount(): QueryCacheResult<InboxCount> {
  const version = useDataVersion(['signals', 'intel'])
  return useQueryCache<InboxCount>(INBOX_COUNT_KEY, fetchInboxCount, {
    staleTime: 30_000,
    version,
  })
}
