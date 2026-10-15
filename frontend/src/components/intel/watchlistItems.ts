import type { WatchPulseRow } from '../../types/watchPulse'
import type { WatchlistItem } from './WatchlistsSection'

// A held name with no watchlist item yet (auto-sync lags) gets a transient one for the alerts
// editor — alerts are keyed by ticker, and an id-less item can't be "removed" from a list.
export function alertItemForRow(items: WatchlistItem[], row: WatchPulseRow): WatchlistItem {
  const existing = items.find((i) => i.type === 'ticker' && i.value.toUpperCase() === row.ticker)
  return (
    existing ?? {
      id: '',
      type: 'ticker',
      value: row.ticker,
      label: row.name,
      asset_class: 'equity',
      monitor_news: true,
      added_by: 'auto',
      note: '',
      watchlist_ids: [],
      instrument_id: row.instrument_id ?? undefined,
    }
  )
}
