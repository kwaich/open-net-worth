import { describe, it, expect } from 'vitest'
import { appendLiveTail, buildIntradayTape, chartEmptyReason, type TailPoint } from './priceTail'

const BARS: TailPoint[] = [
  { date: '2026-06-15T11:30:00Z', price: 37.5 },
  { date: '2026-06-15T11:55:00Z', price: 37.8 }, // last bar
]
const LAST = new Date('2026-06-15T11:55:00Z').getTime()
const NOW = new Date('2026-06-15T12:20:00Z').getTime() // 25 min after last bar

describe('appendLiveTail', () => {
  it('appends the live quote as the final point on 1D when it is newer than the last bar', () => {
    const out = appendLiveTail(BARS, '1D', 37.84, '2026-06-15T12:15:00Z', NOW)
    expect(out).toHaveLength(3)
    // Degenerate bar: the live tick has no real OHLC, so open/high/low/close all equal price.
    expect(out[out.length - 1]!).toEqual({
      date: '2026-06-15T12:15:00Z',
      price: 37.84,
      open: 37.84,
      high: 37.84,
      low: 37.84,
      close: 37.84,
    })
    expect(BARS).toHaveLength(2) // input not mutated
  })

  it('also extends the 5D intraday tape', () => {
    const out = appendLiveTail(BARS, '5D', 40, '2026-06-15T12:15:00Z', NOW)
    expect(out).toHaveLength(3)
    expect(out[2]!.price).toBe(40)
  })

  it('uses `now` when the live timestamp is missing', () => {
    const out = appendLiveTail(BARS, '1D', 37.9, null, NOW)
    expect(out).toHaveLength(3)
    expect(new Date(out[2]!.date).getTime()).toBe(NOW)
  })

  it('does NOT append on daily+ timeframes (granularity mismatch)', () => {
    expect(appendLiveTail(BARS, '1M', 37.9, null, NOW)).toBe(BARS)
    expect(appendLiveTail(BARS, 'MAX', 37.9, null, NOW)).toBe(BARS)
  })

  it('skips when the live timestamp is not newer than the last bar (no dupe/backwards point)', () => {
    expect(appendLiveTail(BARS, '1D', 37.9, '2026-06-15T11:55:00Z', LAST)).toBe(BARS)
    expect(appendLiveTail(BARS, '1D', 37.9, '2026-06-15T11:40:00Z', NOW)).toBe(BARS)
  })

  it('skips when there is no live price or no series to attach to', () => {
    expect(appendLiveTail(BARS, '1D', null, '2026-06-15T12:15:00Z', NOW)).toBe(BARS)
    expect(appendLiveTail(BARS, '1D', undefined, null, NOW)).toBe(BARS)
    expect(appendLiveTail([], '1D', 37.9, null, NOW)).toEqual([])
  })
})

describe('buildIntradayTape', () => {
  const LIVE_AT = '2026-06-15T12:15:00Z'

  it('passes through untouched when the tape already has 2+ points', () => {
    const out = buildIntradayTape(BARS, '1D', null, null, NOW, 37.0, true)
    expect(out).toBe(BARS)
  })

  it('still appends the live tail on top of real bars (composes appendLiveTail)', () => {
    const out = buildIntradayTape(BARS, '1D', 37.84, LIVE_AT, NOW, 37.0, true)
    expect(out).toHaveLength(3)
    expect(out[2]!.price).toBe(37.84)
  })

  it('seeds prev close → live quote when the delayed feed has no bars yet (market open)', () => {
    const out = buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, true)
    expect(out).toHaveLength(2)
    expect(out[0]!).toMatchObject({ price: 39.1, label: 'Prev close' })
    expect(out[1]!).toEqual({ date: LIVE_AT, price: 38.33 })
    // Seed timestamp orders BEFORE the live point.
    expect(new Date(out[0]!.date).getTime()).toBeLessThan(new Date(out[1]!.date).getTime())
  })

  it('never invents a previous-close seed for crypto with zero or one real observation', () => {
    const noBars = buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, true, false)
    const oneBar = buildIntradayTape(
      [BARS[0]!],
      '1D',
      37.9,
      '2026-06-15T11:00:00Z',
      NOW,
      39.1,
      true,
      false,
    )

    expect(noBars).toEqual([])
    expect(oneBar).toEqual([BARS[0]!])
  })

  it('keeps the equity opening seed when synthetic seeding is allowed', () => {
    const out = buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, true, true)

    expect(out).toHaveLength(2)
    expect(out[0]).toMatchObject({ price: 39.1, label: 'Prev close' })
  })

  it('prepends prev close when only a single drawable point exists', () => {
    const oneBar = [BARS[0]!]
    // Live quote older than the bar (the delayed-quote case) — no tail, so 1 point.
    const out = buildIntradayTape(oneBar, '1D', 37.9, '2026-06-15T11:00:00Z', NOW, 39.1, true)
    expect(out).toHaveLength(2)
    expect(out[0]!).toMatchObject({ price: 39.1, label: 'Prev close' })
    expect(out[1]!).toBe(oneBar[0]!)
  })

  it('never seeds when the market is closed, prev close is unknown, or timeframe is not 1D', () => {
    expect(buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, false)).toEqual([])
    expect(buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, null, true)).toEqual([])
    expect(buildIntradayTape([], '5D', 38.33, LIVE_AT, NOW, 39.1, true)).toEqual([])
    expect(buildIntradayTape([], '1M', 38.33, LIVE_AT, NOW, 39.1, true)).toEqual([])
  })

  it('stays empty with no live price to anchor the seed on', () => {
    expect(buildIntradayTape([], '1D', null, null, NOW, 39.1, true)).toEqual([])
  })

  it('dates the seed at the session open when the caller knows it (fixed-grid first slot)', () => {
    // KSTL.DE at the XETRA open: the 1D chart's fixed 1-minute grid starts at the session
    // open and never visits an earlier bucket — a seed at the viewer's local midnight (any
    // viewer whose midnight precedes the open: Berlin, Singapore, …) was silently dropped,
    // leaving one point and a blank pane. Anchoring at the open lands it in the first slot.
    const OPEN = '2026-06-15T07:00:00.000Z'
    const out = buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, true, true, OPEN)
    expect(out).toHaveLength(2)
    expect(out[0]!).toEqual({ date: OPEN, price: 39.1, label: 'Prev close' })
    expect(out[1]!).toEqual({ date: LIVE_AT, price: 38.33 })
  })

  it('falls back to the local-midnight anchor when the session open is not before the tape', () => {
    // A quote timestamped BEFORE the open (stale header quote) — the open can't order the
    // seed ahead of it, so keep the old ordering-only anchor rather than emit a backwards point.
    const LATE_OPEN = '2026-06-15T13:00:00Z'
    const out = buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, true, true, LATE_OPEN)
    expect(out).toHaveLength(2)
    expect(new Date(out[0]!.date).getTime()).toBeLessThan(new Date(LIVE_AT).getTime())
    expect(out[0]!.date).not.toBe(LATE_OPEN)
    // Same fallback for an unparseable open.
    const bad = buildIntradayTape([], '1D', 38.33, LIVE_AT, NOW, 39.1, true, true, 'nope')
    expect(new Date(bad[0]!.date).getTime()).toBeLessThan(new Date(LIVE_AT).getTime())
  })
})

describe('chartEmptyReason', () => {
  const base = { timeframe: '1D' as const, intradayAvailable: true, marketOpen: true }

  it('draws (null) once the live tail makes a single real bar a 2-point line', () => {
    // The ONWAI/XETRA case: 1 real bar + the live "now" point = 2 drawable points.
    expect(chartEmptyReason({ ...base, drawablePoints: 2 })).toBeNull()
  })

  it('reports a delayed feed when 1D cannot draw while the market is open', () => {
    expect(chartEmptyReason({ ...base, drawablePoints: 1, marketOpen: true })).toBe('delayed')
    expect(chartEmptyReason({ ...base, drawablePoints: 0, marketOpen: true })).toBe('delayed')
  })

  it('keeps the intraday-unavailable hint for a closed market with no intraday data', () => {
    expect(
      chartEmptyReason({
        timeframe: '1D',
        drawablePoints: 0,
        intradayAvailable: false,
        marketOpen: false,
      }),
    ).toBe('intraday-unavailable')
  })

  it('falls back to not-enough for longer timeframes with too few points', () => {
    expect(
      chartEmptyReason({
        timeframe: '1M',
        drawablePoints: 1,
        intradayAvailable: false,
        marketOpen: false,
      }),
    ).toBe('not-enough')
    // Closed market, one stale 1D bar, no live tail → generic, not "delayed".
    expect(
      chartEmptyReason({
        timeframe: '1D',
        drawablePoints: 1,
        intradayAvailable: true,
        marketOpen: false,
      }),
    ).toBe('not-enough')
  })
})
