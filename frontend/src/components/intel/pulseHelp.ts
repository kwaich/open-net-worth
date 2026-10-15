// Plain-words help for the pulse strip — what each flag means and how rows are ranked. The
// thresholds mirror backend/services/watch_rules.py (keep them in step when those change).

import { fDate, fPrice, parseDate } from '../../utils/format'
import type { NextEvent, TrendPeriod, WatchPulseRow } from '../../types/watchPulse'

export const FINDING_HELP: Record<string, string> = {
  big_move:
    'A day move measured against this stock’s own usual daily swing over the last 60 trading days — 2× usual is worth a look, 3× is unusual. A calm stock can be flagged at 2%; a volatile one isn’t flagged at 6%. Without enough history: 5% for a stock, 6% commodity, 10% crypto.',
  company_move:
    'The market (your benchmark, scaled by how much this stock usually moves with it) doesn’t explain today’s move — something about the company does.',
  market_move:
    'Mostly the market: the stock moved about as much as it usually does when your benchmark moves like this.',
  sector_move: 'Moving with its sector — its sector fund moved about as much today.',
  drawdown:
    'Down this far from its highest close of the last 20 trading days — deeper than this stock’s usual swings would explain.',
  streak: 'Several days in a row in the same direction, with the total change over the run.',
  momentum:
    'This month’s change compared with your benchmark’s — a gap bigger than this stock’s usual monthly swing.',
  crossed_200d:
    'Crossed its 200-day average in the last few sessions — still news for a few days, not just the day it happened.',
  possible_split:
    'Today’s change matches a stock split (for example 4-for-1 → −75%) on ordinary volume, so price-based flags are paused — the price feed has adjusted, not crashed.',
  entered_buy_zone: 'The price just moved into the buy zone you set for this ticker.',
  in_buy_zone: 'The price is inside the buy zone you set.',
  crossed_below_fair_value: 'The price just fell 10% or more below the fair value you set.',
  below_fair_value: 'The price is 10% or more below the fair value you set.',
  above_fair_value: 'The price is 10% or more above the fair value you set.',
  breakout: 'Trading at its highest price of the past 52 weeks.',
  near_high: 'Within 2% of its 52-week high.',
  breakdown: 'Trading at its lowest price of the past 52 weeks.',
  near_low: 'Within 3% of its 52-week low.',
  discount: '25% or more below its 52-week high — a pullback worth knowing about, not a verdict.',
  reclaim_200d:
    'Closed above its 200-day average after being below it — often read as a longer-term trend turning up.',
  lost_200d:
    'Fell below its 200-day average after being above it — often read as a longer-term trend turning down.',
  reclaim_50d: 'Moved back above its 50-day average — a shorter-term trend turning up.',
  lost_50d: 'Dropped below its 50-day average — a shorter-term trend turning down.',
  volume:
    'Trading volume on pace for 2.5× or more a normal day — measured against how much normally trades by this time of the session, so a busy morning isn’t missed.',
  earnings_soon: 'Reports earnings within a week. Prices often move sharply around the report.',
  alert_near: 'Within 2% of one of your own price alerts.',
}

export type PulseSort = 'attention' | 'today' | 'trend' | 'high' | 'earnings'

export const PULSE_SORTS: { value: PulseSort; label: string }[] = [
  { value: 'attention', label: 'Most urgent' },
  { value: 'today', label: 'Biggest move today' },
  { value: 'trend', label: 'Most ahead or behind' },
  { value: 'high', label: 'Closest to 52-week high' },
  { value: 'earnings', label: 'Next event soonest' },
]

export function isPulseSort(v: unknown): v is PulseSort {
  return PULSE_SORTS.some((s) => s.value === v)
}

const last = (n: number | null | undefined) => (n == null ? Number.POSITIVE_INFINITY : n)

// Over the trend period shown: the move against the benchmark, else the plain move.
function trendMove(r: WatchPulseRow, period: TrendPeriod): number | null {
  const s = r.trend?.[period]
  if (!s) return null
  return Math.abs(s.vs_bench_pct ?? s.pct)
}

export function sortPulseRows(
  rows: WatchPulseRow[],
  sort: PulseSort,
  period: TrendPeriod = '1M',
): WatchPulseRow[] {
  const copy = [...rows]
  if (sort === 'today') copy.sort((a, b) => Math.abs(b.day_pct ?? 0) - Math.abs(a.day_pct ?? 0))
  if (sort === 'trend')
    copy.sort((a, b) => (trendMove(b, period) ?? -1) - (trendMove(a, period) ?? -1))
  // from_high_pct is ≤ 0 (how far below the high); closest-to-high = smallest distance.
  const gap = (r: WatchPulseRow) => (r.from_high_pct == null ? null : Math.abs(r.from_high_pct))
  if (sort === 'high') copy.sort((a, b) => last(gap(a)) - last(gap(b)))
  const nextIn = (r: WatchPulseRow) => r.next_event?.in_days ?? r.earnings_in_days
  if (sort === 'earnings') copy.sort((a, b) => last(nextIn(a)) - last(nextIn(b)))
  return copy // 'attention' keeps the server's ranking; a TickerDesk floats stories needing a decision first
}

// ── Row cell text ────────────────────────────────────────────────────────────

export function toneColor(n: number | null | undefined): string {
  if (n == null || n === 0) return 'var(--faint)'
  return n > 0 ? 'var(--good)' : 'var(--bad)'
}

// A level short enough to sit either side of the bar: whole units from 1,000 up.
export function fLevel(v: number): string {
  return v >= 1000 ? Math.round(v).toLocaleString() : fPrice(v)
}

/** A magnitude at one decimal under 10, whole from 10 up: "1.2", "18" — decided on the ROUNDED
 *  value, so 9.96 reads "10", never "10.0". */
export function compact1(a: number): string {
  return Math.round(a * 10) / 10 >= 10 ? a.toFixed(0) : a.toFixed(1)
}

/** A signed percent at one decimal under 10, whole from 10 up: "+1.2%", "-18%". A value that
 *  rounds to zero reads "+0.0%" (never "-0.0%"); a missing one reads "—". */
export function pct1(n: number): string {
  if (!Number.isFinite(n)) return '—'
  const shown = compact1(Math.abs(n))
  return (n < 0 && Number(shown) !== 0 ? '-' : '+') + shown + '%'
}

export function nextEventText(e: NextEvent): {
  main: string
  date: string
  soon: boolean
  title: string
} {
  const word = e.kind === 'earnings' ? 'Earnings' : 'Ex-div'
  const when = e.in_days === 0 ? 'today' : e.in_days === 1 ? 'tomorrow' : `${e.in_days}d`
  const date = parseDate(e.at) ? fDate(e.at) : ''
  return {
    main: `${word} ${when}`,
    date,
    soon: e.in_days <= 7,
    title:
      e.kind === 'earnings'
        ? 'Next earnings report — prices often move sharply around it'
        : 'Ex-dividend date — you must own the shares before it to receive the next dividend',
  }
}

const KIND_WORDS: Record<string, string> = {
  company: 'company',
  market: 'with market',
  sector: 'with sector',
}

/** The line under today's move: how unusual it is for THIS stock, and what drove it. */
export function moveNote(
  move: WatchPulseRow['move'],
): { text: string; loud: boolean; title: string } | null {
  if (!move || move.z == null) return null
  const a = Math.abs(move.z)
  const usual =
    move.usual_pct != null ? ` (it usually moves about ${move.usual_pct.toFixed(1)}% a day)` : ''
  if (a < 1.5)
    return { text: 'normal day', loud: false, title: `An ordinary day for this stock${usual}` }
  const kind = a >= 2 && move.kind ? ` · ${KIND_WORDS[move.kind] ?? move.kind}` : ''
  return {
    text: `${compact1(a)}× usual${kind}`,
    loud: a >= 2,
    title: `About ${a.toFixed(1)}× this stock’s usual daily move${usual}`,
  }
}
