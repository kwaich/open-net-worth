import { useEffect, useMemo, useState } from 'react'
import { authFetch } from '../../api/client'
import { useQueryCache } from '../../state/queryCache'
import { INTEL_SIGNALS_KEY } from '../../hooks/useIntelStoryIndex'
import type { StoryArc, TickerMeta } from './StoryArcCard'
import type { PriceAlert, Watchlist, WatchlistItem } from './WatchlistsSection'
import type { MonitoringConfig } from './monitoringConfig'

interface WatchlistData {
  items: WatchlistItem[]
  config: MonitoringConfig
  watchlists: Watchlist[]
}

async function getJson<T>(url: string): Promise<T> {
  const response = await authFetch(url)
  if (!response.ok) throw new Error(String(response.status))
  return response.json()
}

/** What the Intel tab reads — the stories, the watchlists, the price alerts and the watched
 *  tickers' prices — polled while the tab is open. Stories every 15s; the watchlist and alerts
 *  every minute (alerts are their own store — ADR 0014 — so a background change by Lucius or
 *  another tab must still surface). */
export function useIntelData() {
  const storiesQuery = useQueryCache<StoryArc[]>(
    INTEL_SIGNALS_KEY,
    () => getJson<StoryArc[]>(INTEL_SIGNALS_KEY),
    { staleTime: 0 },
  )
  const watchlistQuery = useQueryCache<WatchlistData>(
    '/api/watchlist',
    () => getJson<WatchlistData>('/api/watchlist'),
    { staleTime: 0 },
  )
  const alertsQuery = useQueryCache<PriceAlert[]>(
    '/api/alerts',
    async () => (await getJson<{ alerts?: PriceAlert[] }>('/api/alerts')).alerts ?? [],
    { staleTime: 0 },
  )
  const watchlist = watchlistQuery.data ?? null
  const watchedTickers = (watchlist?.items ?? [])
    .filter((item) => item.type === 'ticker')
    .map((item) => item.value)
  const pricesKey = `/api/prices?tickers=${watchedTickers.join(',')}&include_names=true`
  const pricesQuery = useQueryCache<Record<string, TickerMeta>>(
    pricesKey,
    async () => {
      if (!watchedTickers.length) return {}
      const data = await getJson<{
        prices?: Record<string, number>
        names?: Record<string, string>
        exchanges?: Record<string, string>
      }>(pricesKey)
      return Object.fromEntries(
        watchedTickers.map((t) => [
          t,
          { price: data.prices?.[t], name: data.names?.[t], exchange: data.exchanges?.[t] },
        ]),
      )
    },
    { staleTime: 0 },
  )

  const loadStories = storiesQuery.refresh
  const loadWatchlist = watchlistQuery.refresh
  const loadAlerts = alertsQuery.refresh
  // A clock tick re-renders the relative times ("5m ago") between polls.
  const [, setTick] = useState(0)
  useEffect(() => {
    const storyPoll = setInterval(() => void loadStories(), 15_000)
    const watchPoll = setInterval(() => {
      void loadWatchlist()
      void loadAlerts()
    }, 60_000)
    const clockTick = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => {
      clearInterval(storyPoll)
      clearInterval(watchPoll)
      clearInterval(clockTick)
    }
  }, [loadStories, loadWatchlist, loadAlerts])

  const stories = useMemo(() => storiesQuery.data ?? [], [storiesQuery.data])
  return {
    stories,
    alerts: alertsQuery.data ?? [],
    watchlist,
    items: useMemo(() => watchlist?.items ?? [], [watchlist]),
    watchlists: useMemo(() => watchlist?.watchlists ?? [], [watchlist]),
    tickerMeta: pricesQuery.data ?? {},
    watchlistError: Boolean(watchlistQuery.error && !watchlist),
    loading: [storiesQuery, watchlistQuery, alertsQuery].some((q) => q.initialLoading && !q.error),
    loadWatchlist,
    loadAlerts,
  }
}
