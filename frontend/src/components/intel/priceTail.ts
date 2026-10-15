import type { SeriesTimeframe } from '../../types/assetDetail'

/** A price-series point, optionally carrying full OHLCV — the same per-point shape
 *  `AssetPriceSeries.points` sends from the backend (`price` stays the close alias for
 *  any consumer that only wants the line). The OHLCV fields are absent/undefined on
 *  legacy data or whenever `ohlc_available` is false for that series. */
export interface TailPoint {
  date: string
  price: number
  /** X-axis label override — set on synthetic points (e.g. "Prev close") whose
   *  timestamp is an ordering anchor, not a real trade time. */
  label?: string
  open?: number
  high?: number
  low?: number
  close?: number
  volume?: number
}

/**
 * Append the live quote as a trailing point on the INTRADAY tape (1D / 5D) so the
 * chart's right edge reflects "now" — the same live price the header shows — instead
 * of the latest (vendor-delayed) intraday bar. Daily+ timeframes are left untouched.
 *
 * The live quote is a single tick, not a real OHLC bar, so it's appended as a
 * DEGENERATE bar (open = high = low = close = the live price) — this lets it draw
 * correctly on Candle/Bar charts too, exactly like a bar that has traded at one price
 * so far. `volume` is left undefined (we don't have an intraday-so-far volume figure
 * for the live tick).
 *
 * Pure (takes `now` rather than reading the clock) so it's unit-testable. Skips the
 * append when there's no live price, no series to attach to, or the live timestamp is
 * not newer than the last bar (avoids a duplicate or a backwards point).
 */
export function appendLiveTail(
  points: TailPoint[],
  timeframe: SeriesTimeframe,
  livePrice: number | null | undefined,
  liveAt: string | null | undefined,
  now: number,
): TailPoint[] {
  const intraday = timeframe === '1D' || timeframe === '5D'
  if (!intraday || livePrice == null || points.length === 0) return points

  const lastTs = new Date(points[points.length - 1]!.date).getTime()
  const liveTs = liveAt ? new Date(liveAt).getTime() : now
  if (!Number.isFinite(liveTs) || liveTs <= lastTs) return points

  return [
    ...points,
    {
      date: liveAt ?? new Date(liveTs).toISOString(),
      price: livePrice,
      open: livePrice,
      high: livePrice,
      low: livePrice,
      close: livePrice,
    },
  ]
}

/**
 * Build the full 1D drawable tape: the real bars plus the live tail, then — while the
 * market is OPEN but the delayed vendor feed hasn't produced 2 drawable points yet (the
 * first ~15 min of a XETRA session) — seed the line from the previous close to the live
 * quote, so the chart draws from the first second of the session instead of sitting on
 * "Live data delayed" while the header price visibly updates. Real bars replace the seed
 * as soon as 2 of them (or 1 bar + a newer live tail) exist.
 *
 * The prev-close point is dated at `seedAt` — the session open, when the caller knows it —
 * so it lands in the FIRST slot of the 1D chart's fixed 1-minute grid (which starts at the
 * open and never visits an earlier bucket: a seed at the viewer's local midnight was dropped
 * there for every viewer whose midnight precedes the open, leaving one point and a blank
 * pane — owner-reported 2026-09-03, KSTL.DE at the XETRA open from GMT+2). Without a usable
 * open (unknown, or not strictly before the tape) it falls back to the live point's local
 * midnight purely for ordering. Its `label` is what the hover shows in place of the date.
 */
export function buildIntradayTape(
  points: TailPoint[],
  timeframe: SeriesTimeframe,
  livePrice: number | null | undefined,
  liveAt: string | null | undefined,
  now: number,
  prevClose?: number | null,
  marketOpen = false,
  allowSyntheticSeed = true,
  seedAt?: string | null,
): TailPoint[] {
  let tape = appendLiveTail(points, timeframe, livePrice, liveAt, now)
  if (
    !allowSyntheticSeed ||
    timeframe !== '1D' ||
    !marketOpen ||
    prevClose == null ||
    tape.length >= 2
  )
    return tape
  if (tape.length === 0) {
    // No bars at all: anchor the seed on the header quote — without it there is no line.
    if (livePrice == null) return tape
    const liveTs = liveAt ? new Date(liveAt).getTime() : now
    const ts = Number.isFinite(liveTs) ? liveTs : now
    tape = [{ date: liveAt ?? new Date(ts).toISOString(), price: livePrice }]
  }
  const firstTs = new Date(tape[0]!.date).getTime()
  return [{ date: seedDate(seedAt, firstTs, now), price: prevClose, label: 'Prev close' }, ...tape]
}

function seedDate(seedAt: string | null | undefined, firstTs: number, now: number): string {
  const openTs = seedAt ? new Date(seedAt).getTime() : NaN
  if (Number.isFinite(openTs) && Number.isFinite(firstTs) && openTs < firstTs) return seedAt!
  const seedDay = new Date(Number.isFinite(firstTs) ? firstTs : now)
  seedDay.setHours(0, 0, 0, 0)
  return seedDay.toISOString()
}

export type ChartEmptyReason = 'delayed' | 'intraday-unavailable' | 'not-enough'

/**
 * Why a chart can't draw, given the LIVE-extended point count (the real bars plus any live
 * tail — see `appendLiveTail`). Returns null when there are ≥2 points to draw a line.
 *
 * A single real intraday bar plus the live "now" point is a drawable line, so the common
 * vendor-delay case (e.g. Yahoo's XETRA tape frozen at the open) draws instead of reading as
 * "no data". When 1D still can't draw while the market is open, the feed is just delayed —
 * say so, rather than the generic "not enough history" which reads as if WE lack the data.
 */
export function chartEmptyReason(opts: {
  drawablePoints: number
  timeframe: SeriesTimeframe
  intradayAvailable: boolean
  marketOpen: boolean
}): ChartEmptyReason | null {
  if (opts.drawablePoints >= 2) return null
  const is1D = opts.timeframe === '1D'
  if (is1D && opts.marketOpen) return 'delayed'
  if (is1D && !opts.intradayAvailable) return 'intraday-unavailable'
  return 'not-enough'
}
