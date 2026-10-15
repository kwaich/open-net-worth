// useWatchlistBoard — the Watchlist sub-tab's read (GET /api/watchlist/quotes), polled every
// minute while mounted, plus the pure sort the cards and the table share.

import { useEffect } from 'react'
import { authFetch } from '../../../api/client'
import { useQueryCache } from '../../../state/queryCache'
import type { WatchQuoteBoard, WatchQuoteRow } from '../../../types/watchlistQuotes'

export const WATCH_QUOTES_KEY = '/api/watchlist/quotes'
const POLL_MS = 60_000

async function fetchBoard(): Promise<WatchQuoteBoard> {
  const res = await authFetch(WATCH_QUOTES_KEY)
  if (!res.ok) throw new Error(String(res.status))
  return res.json()
}

export function useWatchlistBoard() {
  const q = useQueryCache<WatchQuoteBoard>(WATCH_QUOTES_KEY, fetchBoard, { staleTime: 0 })
  const refresh = q.refresh
  useEffect(() => {
    const id = setInterval(() => void refresh(), POLL_MS)
    return () => clearInterval(id)
  }, [refresh])
  return {
    board: q.data ?? null,
    loading: q.initialLoading && !q.error,
    error: Boolean(q.error && !q.data),
    refresh,
  }
}

export type WatchSortKey =
  | 'sym'
  | 'price'
  | 'change'
  | 'change_pct'
  | 'volume'
  | 'avg_volume'
  | 'market_cap'
  | 'range_pos'
  | 'trailing_pe'
  | 'day_pl'
  | 'unrealized'
  | 'list'

export interface WatchSort {
  key: WatchSortKey
  dir: number
}

function sortValue(r: WatchQuoteRow, key: WatchSortKey): number | string | null {
  switch (key) {
    case 'sym':
      return r.ticker
    case 'day_pl':
      return r.position?.day_pl ?? null
    case 'unrealized':
      return r.position?.unrealized ?? null
    case 'list':
      return null
    default:
      return r[key]
  }
}

/** `list` keeps the watchlist's own order. Unknown figures always sink, whatever the direction. */
export function sortWatchRows(rows: WatchQuoteRow[], sort: WatchSort): WatchQuoteRow[] {
  if (sort.key === 'list') return rows
  return [...rows].sort((a, b) => {
    const va = sortValue(a, sort.key)
    const vb = sortValue(b, sort.key)
    if (va == null && vb == null) return 0
    if (va == null) return 1
    if (vb == null) return -1
    const cmp = typeof va === 'string' ? va.localeCompare(String(vb)) : va - (vb as number)
    return cmp * sort.dir
  })
}

/** Rows on one watchlist, narrowed by a symbol/name search. */
export function rowsForList(rows: WatchQuoteRow[], listId: string, search: string) {
  const q = search.trim().toLowerCase()
  return rows.filter(
    (r) =>
      r.watchlist_ids.includes(listId) && (!q || `${r.ticker} ${r.name}`.toLowerCase().includes(q)),
  )
}
