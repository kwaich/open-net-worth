// watchColumns — the Watchlist table's column registry. One entry per column the owner can show
// or hide from Manage Columns; the choice (and its order) persists per browser.

import type { ReactNode } from 'react'
import type { WatchQuoteRow } from '../../../types/watchlistQuotes'
import type { WatchSortKey } from './useWatchlistBoard'
import { fBase, fCompact, fDate, fPct, fPrice, fUnitPrice } from '../../../utils/format'

export type WatchColumnGroup = 'Prices' | 'High / Low / Volume' | 'Fundamentals' | 'Position & P&L'

export interface WatchColumn {
  key: string
  label: string
  group: WatchColumnGroup
  width: number
  sortKey?: WatchSortKey
  /** Sensitive (your own money) — carries the privacy `mask` class. */
  mask?: boolean
  cell: (r: WatchQuoteRow) => ReactNode
}

export function tone(v: number | null | undefined): string {
  if (v == null || v === 0) return 'var(--muted)'
  return v > 0 ? 'var(--good)' : 'var(--bad)'
}

/** Decimals a quote is shown to: cents for a stock or fund, full precision for a coin
 *  (a $0.0009 token must not round to $0.00). */
export function quoteDigits(r: Pick<WatchQuoteRow, 'asset_class'>): number | undefined {
  return r.asset_class === 'crypto' ? undefined : 2
}

/** Signed per-share change ("+3.81", "−0.24"), to the quote's own precision. */
export function fChange(v: number | null, digits?: number): string {
  if (v == null) return '—'
  if (v === 0) return '0.00'
  const abs = Math.abs(v)
  return (v > 0 ? '+' : '−') + fPrice(abs, digits ?? (abs >= 1 ? 2 : 4))
}

const px = (r: WatchQuoteRow, v: number | null) => (v == null ? '—' : fPrice(v, quoteDigits(r)))

export function fSignedBase(v: number | null | undefined): string {
  if (v == null) return '—'
  return (v >= 0 ? '+' : '−') + fBase(Math.abs(v))
}

const num = (v: number | null, frac = 2) => (v == null ? '—' : fCompact(v, frac))
const colored = (v: number | null, text: string) => <span style={{ color: tone(v) }}>{text}</span>

function rangeBar(r: WatchQuoteRow): ReactNode {
  if (r.range_pos == null) return <>—</>
  return (
    <span
      className="mk-wrange"
      title={`52w ${px(r, r.week52_low)} – ${px(r, r.week52_high)}`}
      aria-label={`${Math.round(r.range_pos * 100)}% of the 52-week range`}
    >
      <span className="mk-wrange-track">
        <span className="mk-wrange-dot" style={{ left: `${r.range_pos * 100}%` }} />
      </span>
    </span>
  )
}

export const WATCH_COLUMNS: WatchColumn[] = [
  {
    key: 'last',
    label: 'Last',
    group: 'Prices',
    width: 96,
    sortKey: 'price',
    cell: (r) => fUnitPrice(r.price, r.currency ?? undefined, quoteDigits(r)),
  },
  {
    key: 'change',
    label: 'Change',
    group: 'Prices',
    width: 84,
    sortKey: 'change',
    cell: (r) => colored(r.change, fChange(r.change, quoteDigits(r))),
  },
  {
    key: 'change_pct',
    label: 'Change %',
    group: 'Prices',
    width: 84,
    sortKey: 'change_pct',
    cell: (r) => colored(r.change_pct, fPct(r.change_pct, { sign: true })),
  },
  {
    key: 'prev_close',
    label: 'Prev close',
    group: 'Prices',
    width: 90,
    cell: (r) => px(r, r.prev_close),
  },
  {
    key: 'open',
    label: 'Open',
    group: 'Prices',
    width: 84,
    cell: (r) => px(r, r.open),
  },
  {
    key: 'ccy',
    label: 'Cur',
    group: 'Prices',
    width: 56,
    cell: (r) => r.currency ?? '—',
  },
  {
    key: 'volume',
    label: 'Volume',
    group: 'High / Low / Volume',
    width: 84,
    sortKey: 'volume',
    cell: (r) => num(r.volume),
  },
  {
    key: 'avg_volume',
    label: 'Avg volume',
    group: 'High / Low / Volume',
    width: 90,
    sortKey: 'avg_volume',
    cell: (r) => num(r.avg_volume),
  },
  {
    key: 'volume_ratio',
    label: 'Vol / avg',
    group: 'High / Low / Volume',
    width: 76,
    cell: (r) => (r.volume_ratio == null ? '—' : `${r.volume_ratio.toFixed(2)}×`),
  },
  {
    key: 'day_range',
    label: 'Day range',
    group: 'High / Low / Volume',
    width: 132,
    cell: (r) =>
      r.day_low == null || r.day_high == null ? '—' : `${px(r, r.day_low)} – ${px(r, r.day_high)}`,
  },
  {
    key: 'range52',
    label: '52w range',
    group: 'High / Low / Volume',
    width: 110,
    sortKey: 'range_pos',
    cell: rangeBar,
  },
  {
    key: 'week52_low',
    label: '52w low',
    group: 'High / Low / Volume',
    width: 84,
    cell: (r) => px(r, r.week52_low),
  },
  {
    key: 'week52_high',
    label: '52w high',
    group: 'High / Low / Volume',
    width: 84,
    cell: (r) => px(r, r.week52_high),
  },
  {
    key: 'market_cap',
    label: 'Mkt cap',
    group: 'Fundamentals',
    width: 90,
    sortKey: 'market_cap',
    cell: (r) => num(r.market_cap),
  },
  {
    key: 'pe',
    label: 'P/E',
    group: 'Fundamentals',
    width: 64,
    sortKey: 'trailing_pe',
    cell: (r) => (r.trailing_pe == null ? '—' : r.trailing_pe.toFixed(1)),
  },
  {
    key: 'earnings',
    label: 'Earnings',
    group: 'Fundamentals',
    width: 96,
    cell: (r) => (r.earnings_at ? fDate(r.earnings_at) : '—'),
  },
  {
    key: 'pos',
    label: 'Position',
    group: 'Position & P&L',
    width: 84,
    mask: true,
    cell: (r) => (r.position ? r.position.quantity.toLocaleString() : ''),
  },
  {
    key: 'value',
    label: 'Mkt value',
    group: 'Position & P&L',
    width: 100,
    mask: true,
    cell: (r) => (r.position?.value == null ? '' : fBase(r.position.value)),
  },
  {
    key: 'day_pl',
    label: 'Daily P&L',
    group: 'Position & P&L',
    width: 100,
    sortKey: 'day_pl',
    mask: true,
    cell: (r) => (r.position ? colored(r.position.day_pl, fSignedBase(r.position.day_pl)) : ''),
  },
  {
    key: 'unrealized',
    label: 'Unrealized P&L',
    group: 'Position & P&L',
    width: 116,
    sortKey: 'unrealized',
    mask: true,
    cell: (r) =>
      r.position ? colored(r.position.unrealized, fSignedBase(r.position.unrealized)) : '',
  },
]

export const WATCH_COLUMN_GROUPS: WatchColumnGroup[] = [
  'Prices',
  'High / Low / Volume',
  'Fundamentals',
  'Position & P&L',
]

export const DEFAULT_WATCH_COLUMNS = [
  'last',
  'change',
  'change_pct',
  'volume',
  'range52',
  'market_cap',
  'pos',
  'day_pl',
  'unrealized',
]

const COLS_KEY = 'inv-watch-columns'
const BY_KEY = new Map(WATCH_COLUMNS.map((c) => [c.key, c]))

export function loadWatchColumns(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(COLS_KEY) || 'null')
    if (Array.isArray(v)) {
      const known = v.filter((k): k is string => typeof k === 'string' && BY_KEY.has(k))
      if (known.length) return known
    }
  } catch {
    /* fall through */
  }
  return DEFAULT_WATCH_COLUMNS
}

export function saveWatchColumns(keys: string[]): void {
  try {
    localStorage.setItem(COLS_KEY, JSON.stringify(keys))
  } catch {
    /* private mode — the choice lasts this session only */
  }
}

export function columnsFor(keys: string[]): WatchColumn[] {
  return keys.map((k) => BY_KEY.get(k)).filter((c): c is WatchColumn => !!c)
}
