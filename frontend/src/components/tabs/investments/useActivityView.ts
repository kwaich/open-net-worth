import { useCallback, useEffect, useState } from 'react'
import { useDataVersion } from '../../../hooks/useDataVersion'
import { authFetch } from '../../../api/client'
import type { RealizedPnl } from '../../../types/portfolio'
import { ViewCache } from './viewCache'

// Module-level cache — keyed by "dateFrom|dateTo", populated on first successful
// fetch and reused on re-mount. The tick (retry) key bypasses the cache.
const _cache = new ViewCache<ActivityViewModel>('activity')

// ── API response shapes ───────────────────────────────────────────────────────

// Flat event shape emitted by GET /api/investments/activity (services/activity.py).
// No nested objects — every field is top-level.
export interface ActivityRow {
  source_id: string
  source_kind: string
  date: string
  type: string
  symbol: string | null
  name: string | null
  detail: string | null
  account_id: string | null
  account_name: string | null
  amount_base: number
  signed: boolean
  currency: string
  instrument_id: string | null
  links: Record<string, string | null>
}

interface ActivitySummary {
  count_30d: number
  net_invested_30d: number
  income_30d: number
  fees_30d: number
}

interface ActivityFeedResponse {
  base: string
  events: ActivityRow[]
  summary: ActivitySummary
}

// ── View model ───────────────────────────────────────────────────────────────

export interface ActivitySummaryCards {
  count30d: number
  netInvested30d: number
  income30d: number
  fees30d: number
  baseCurrency: string
}

export interface ActivityViewModel {
  rows: ActivityRow[]
  total: number
  summary: ActivitySummaryCards
  realizedPnl: RealizedPnl | null
  baseCurrency: string
}

export interface UseActivityViewResult {
  data: ActivityViewModel | null
  loading: boolean
  error: boolean
  retry: () => void
}

// ── Fetchers ──────────────────────────────────────────────────────────────────

async function fetchActivityFeed(dateFrom: string, dateTo: string): Promise<ActivityFeedResponse> {
  const r = await authFetch(`/api/investments/activity?date_from=${dateFrom}&date_to=${dateTo}`)
  if (!r.ok) throw new Error(`activity ${r.status}`)
  return r.json()
}

async function fetchRealizedPnl(): Promise<RealizedPnl> {
  const r = await authFetch('/api/realized-pnl')
  if (!r.ok) throw new Error(`realized-pnl ${r.status}`)
  return r.json()
}

/**
 * Fetches the investment activity feed for the given [dateFrom, dateTo] window
 * and the all-time realized P&L summary, mapping both to the view model used
 * by ActivityTab.
 *
 * The date range is driven by ActivityTab's range dropdown. Re-fetches whenever
 * either bound changes. The endpoint supports limit/offset pagination; it is not
 * used here because a time-bounded window (max ~90 days or one quarter) is small
 * enough to fetch in one request. For very high-activity portfolios, a paginated
 * load-more pattern could be added without changing this hook's contract.
 */
export function useActivityView(dateFrom: string, dateTo: string): UseActivityViewResult {
  const cacheKey = `${dateFrom}|${dateTo}`
  const [data, setData] = useState<ActivityViewModel | null>(() => _cache.get(cacheKey) ?? null)
  const [loading, setLoading] = useState(() => _cache.get(cacheKey) == null)
  const [error, setError] = useState(false)
  const [tick, setTick] = useState(0)

  // retry clears the cache entry so a re-fetch skips the stale result.
  const retry = useCallback(() => {
    _cache.invalidate(cacheKey)
    setTick((t) => t + 1)
  }, [cacheKey])

  // Any Lucius write can move activity (trades, ledger, options) — refetch in place on each.
  const anyWriteV = useDataVersion('*')

  useEffect(() => {
    let alive = true
    const cached = _cache.get(cacheKey)
    if (cached != null && tick === 0) {
      setData(cached)
      setLoading(false)
      setError(false)
    } else {
      setLoading(true)
      setError(false)
    }

    Promise.all([fetchActivityFeed(dateFrom, dateTo), fetchRealizedPnl()])
      .then(([feed, pnl]) => {
        if (!alive) return
        const vm: ActivityViewModel = {
          rows: feed.events,
          total: feed.events.length,
          baseCurrency: feed.base,
          summary: {
            count30d: feed.summary.count_30d,
            netInvested30d: feed.summary.net_invested_30d,
            income30d: feed.summary.income_30d,
            fees30d: feed.summary.fees_30d,
            baseCurrency: feed.base,
          },
          realizedPnl: pnl,
        }
        _cache.set(cacheKey, vm)
        setData(vm)
        setLoading(false)
      })
      .catch(() => {
        if (!alive) return
        setError(true)
        setLoading(false)
      })

    return () => {
      alive = false
    }
  }, [dateFrom, dateTo, tick, cacheKey, anyWriteV])

  return { data, loading, error, retry }
}
