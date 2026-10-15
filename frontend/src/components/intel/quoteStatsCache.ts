import type { Stats } from './quoteStatsTypes'

export type QuoteStatsKind = 'equity' | 'crypto'

export interface LoadedQuoteStats {
  stats: Stats | null
  kind: QuoteStatsKind
  currency: string | null
}

// Session cache so a reopen paints the last-known stats instantly (stale-while-revalidate),
// the same shape as useAssetDetail's detailCache / useMovementTimeline's cache. Its own module
// so QuoteStats.tsx exports only the component (react-refresh).
export const quoteStatsCache = new Map<string, LoadedQuoteStats>()

/** Test-only: reset the module cache between cases. */
export function _clearQuoteStatsCache(): void {
  quoteStatsCache.clear()
}
