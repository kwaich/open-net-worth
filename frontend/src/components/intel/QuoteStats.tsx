import { useEffect, useState } from 'react'
import { Skeleton } from '../ui/Skeleton'
import { authFetch } from '../../api/client'
import { quoteStatsCache, type LoadedQuoteStats, type QuoteStatsKind } from './quoteStatsCache'
import type { Stats } from './quoteStatsTypes'
import { fDate, getCurrencySymbol } from '../../utils/format'

const DASH = '—'

function fNum(n: number): string {
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 })
}

function fCompact(n: number): string {
  const a = Math.abs(n)
  if (a >= 1e12) return `${(n / 1e12).toFixed(2)}T`
  if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`
  if (a >= 1e6) return `${(n / 1e6).toFixed(2)}M`
  if (a >= 1e3) return `${(n / 1e3).toFixed(2)}K`
  return fNum(n)
}

type Kind = QuoteStatsKind
type Loaded = LoadedQuoteStats

interface Props {
  instrumentId: string
  currency: string
  /** The asset's class, so the first-paint placeholder already has the right row set. */
  kind?: Kind
}

const EQUITY_LABELS = [
  'Prev Close',
  'Open',
  'Day Range',
  '52W Range',
  'Market Cap',
  'P/E Ratio',
  'EPS',
  'Dividend Yield',
  'Volume',
]
const CRYPTO_LABELS = [
  'Market Cap',
  'Fully Diluted Val.',
  'Volume (24h)',
  'Vol/Market Cap (24h)',
  '24h Range',
  'All-time High',
  'All-time Low',
  'Circulating Supply',
  'Total Supply',
  'Max Supply',
  'Start Date',
]

// Real per-quote stats shown below the chart, like Perplexity's quote bar — Yahoo shape
// for equities, CoinGecko shape for crypto (kind-discriminated by the backend).
export function QuoteStats({ instrumentId, currency, kind: kindHint = 'equity' }: Props) {
  // The section is on the page from the FIRST paint — a skeleton grid with the real labels
  // until the stats land (owner-reproduced 2026-09-21: "the stats grid below the chart is not
  // shown initially, only after the data is loaded" — it popped in late and everything under
  // it, Lucius's take and the movement timeline, jumped down). Hidden only once the response
  // says there is genuinely nothing to show.
  const [view, setView] = useState<{ id: string; loaded: Loaded | null }>(() => ({
    id: instrumentId,
    loaded: quoteStatsCache.get(instrumentId) ?? null,
  }))
  // Instrument changed: reset DURING render (the React-sanctioned "adjust state when a prop
  // changes" pattern, as useMovementTimeline does) — no setState inside an effect.
  if (view.id !== instrumentId) {
    setView({ id: instrumentId, loaded: quoteStatsCache.get(instrumentId) ?? null })
  }

  useEffect(() => {
    let alive = true
    authFetch(`/api/intel/assets/${instrumentId}/stats`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { stats?: Stats; kind?: Kind; currency?: string } | null) => {
        if (!alive) return
        const next: Loaded = {
          stats: d?.stats ?? null,
          kind: d?.kind ?? 'equity',
          currency: d?.currency ?? null,
        }
        quoteStatsCache.set(instrumentId, next)
        setView({ id: instrumentId, loaded: next })
      })
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [instrumentId])

  const loaded =
    view.id === instrumentId ? view.loaded : (quoteStatsCache.get(instrumentId) ?? null)
  if (loaded === null) {
    const labels = kindHint === 'crypto' ? CRYPTO_LABELS : EQUITY_LABELS
    return (
      <section className="panel-section asd-stats" aria-busy="true">
        <div className="asd-stats-grid">
          {labels.map((label) => (
            <div key={label} className="asd-stat">
              <span className="asd-stat-label">{label}</span>
              <span className="asd-stat-value">
                <Skeleton height={12} width="60%" />
              </span>
            </div>
          ))}
        </div>
      </section>
    )
  }
  const { stats, kind, currency: statsCurrency } = loaded
  if (!stats || Object.keys(stats).length === 0) return null

  // Crypto stats come extracted in the backend's display currency (may fall back to USD
  // when CoinGecko doesn't serve the base fiat) — trust the response over the prop then.
  const sym = getCurrencySymbol(kind === 'crypto' ? (statsCurrency ?? currency) : currency)
  const money = (n?: number) => (n == null ? DASH : `${n < 0 ? '-' : ''}${sym}${fNum(Math.abs(n))}`)
  const moneyCompact = (n?: number) => (n == null ? DASH : `${sym}${fCompact(n)}`)
  const range = (lo?: number, hi?: number) =>
    lo != null && hi != null ? `${money(lo)}–${money(hi)}` : DASH // unspaced en dash: fits a phone tile
  const divYield =
    stats.dividend_yield == null
      ? DASH
      : `${(stats.dividend_yield < 1 ? stats.dividend_yield * 100 : stats.dividend_yield).toFixed(2)}%`

  const rows: [string, string][] =
    kind === 'crypto'
      ? [
          ['Market Cap', moneyCompact(stats.market_cap)],
          ['Fully Diluted Val.', moneyCompact(stats.fdv)],
          ['Volume (24h)', moneyCompact(stats.volume_24h)],
          [
            'Vol/Market Cap (24h)',
            stats.vol_to_mcap != null ? `${(stats.vol_to_mcap * 100).toFixed(2)}%` : DASH,
          ],
          ['24h Range', range(stats.day_low, stats.day_high)],
          ['All-time High', money(stats.ath)],
          ['All-time Low', money(stats.atl)],
          [
            'Circulating Supply',
            stats.circulating_supply != null ? fCompact(stats.circulating_supply) : DASH,
          ],
          ['Total Supply', stats.total_supply != null ? fCompact(stats.total_supply) : DASH],
          ['Max Supply', stats.max_supply != null ? fCompact(stats.max_supply) : DASH],
          ['Start Date', stats.genesis_date ? fDate(stats.genesis_date, 'short', 'always') : DASH],
        ]
      : [
          ['Prev Close', money(stats.prev_close)],
          ['Open', money(stats.open)],
          ['Day Range', range(stats.day_low, stats.day_high)],
          ['52W Range', range(stats.week52_low, stats.week52_high)],
          ['Market Cap', stats.market_cap != null ? `${sym}${fCompact(stats.market_cap)}` : DASH],
          ['P/E Ratio', stats.pe_ratio != null ? fNum(stats.pe_ratio) : DASH],
          ['EPS', money(stats.eps)],
          ['Dividend Yield', divYield],
          ['Volume', stats.volume != null ? fCompact(stats.volume) : DASH],
        ]

  return (
    <section className="panel-section asd-stats">
      <div className="asd-stats-grid">
        {rows.map(([label, value]) => (
          <div key={label} className="asd-stat">
            <span className="asd-stat-label">{label}</span>
            <span className="asd-stat-value">{value}</span>
          </div>
        ))}
      </div>
    </section>
  )
}
