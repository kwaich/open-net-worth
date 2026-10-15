import { describe, it, expect } from 'vitest'
import {
  AXIS_LABEL_EDGE_PAD,
  buildFixedRangeGrid,
  capAutoscaleMax,
  edgeToEdgeLogicalRange,
  mainSeriesLastValueOptions,
  niceTicks,
  sessionWindowRange,
  tickMarkFormatterFor,
  usableSessionWindow,
  volumeAutoscaleCap,
  widenAutoscaleToPrice,
} from './assetPriceChartLwc'
import { buildIntradayTape, type TailPoint } from './priceTail'
import { TickMarkType, type UTCTimestamp } from 'lightweight-charts'

// sessionWindowRange: the 1D chart's fixed visible range, sourced from Yahoo's own
// currentTradingPeriod (never a hardcoded local-hour convention — owner ask 2026-08-31).

describe('sessionWindowRange', () => {
  it('spans pre.start through post.end when all three segments are present', () => {
    const bounds = {
      pre: { start: '2026-08-31T08:00:00.000Z', end: '2026-08-31T13:30:00.000Z' },
      regular: { start: '2026-08-31T13:30:00.000Z', end: '2026-08-31T20:00:00.000Z' },
      post: { start: '2026-08-31T20:00:00.000Z', end: '2026-08-31T24:00:00.000Z' },
    }
    const range = sessionWindowRange(bounds)
    expect(range).toEqual({
      from: Math.floor(new Date(bounds.pre.start).getTime() / 1000),
      to: Math.floor(new Date(bounds.post.end).getTime() / 1000),
    })
  })

  it('falls back to regular-only bounds when pre/post are absent', () => {
    const bounds = {
      regular: { start: '2026-08-31T13:30:00.000Z', end: '2026-08-31T20:00:00.000Z' },
    }
    const range = sessionWindowRange(bounds)
    expect(range).toEqual({
      from: Math.floor(new Date(bounds.regular.start).getTime() / 1000),
      to: Math.floor(new Date(bounds.regular.end).getTime() / 1000),
    })
  })

  it('returns null when no bound at all is present', () => {
    expect(sessionWindowRange(null)).toBeNull()
    expect(sessionWindowRange(undefined)).toBeNull()
    expect(sessionWindowRange({})).toBeNull()
  })

  it('returns null when only pre is known — no regular/post end to anchor "to" at all', () => {
    const bounds = { pre: { start: '2026-08-31T08:00:00.000Z', end: '2026-08-31T13:30:00.000Z' } }
    expect(sessionWindowRange(bounds)).toBeNull()
  })
})

// mainSeriesLastValueOptions: regression coverage for 2026-09-04 — KSTL.DE two minutes into the
// XETRA open rendered as if the whole day were already flat, because lightweight-charts' own
// "last value" line stretched from the seed's real (correctly LEFT-anchored) point all the way
// across the fixed grid's hours of not-yet-happened whitespace to the right axis, landing right
// on top of the always-full-width "Prev close" reference line.

describe('mainSeriesLastValueOptions', () => {
  it('hides the built-in last-value line/label while the fixed 1D session grid is active', () => {
    expect(mainSeriesLastValueOptions(true)).toEqual({
      lastValueVisible: false,
      priceLineVisible: false,
    })
  })

  it('leaves the built-in last-value line/label visible outside the fixed grid', () => {
    expect(mainSeriesLastValueOptions(false)).toEqual({
      lastValueVisible: true,
      priceLineVisible: true,
    })
  })
})

// buildFixedRangeGrid: the 1D chart's uniform 1-minute grid. Regression coverage for the
// 2026-09-01 bug — a real bar (or the appended live tail) landing a few seconds PAST the
// minute was silently dropped, since the grid only matched an EXACT multiple of 60s from
// range.from — leaving the chart's own last-plotted value stuck on an older bar while the
// detail header (reading the live quote directly) had already moved on, visibly disagreeing
// with it.

// `to` is the session's END (Yahoo's post.end, e.g. 20:00:00) — a minute bar is stamped at its
// START, so the last bar a session can produce is `to - 60`: 3 slots here, :00 :60 :120.
const RANGE = { from: 0 as UTCTimestamp, to: 180 as UTCTimestamp }

function point(atSeconds: number, price: number): TailPoint {
  return { date: new Date(atSeconds * 1000).toISOString(), price }
}

describe('buildFixedRangeGrid', () => {
  it('plots a point landing a few seconds past its minute — not silently dropped', () => {
    const grid = buildFixedRangeGrid([point(65, 501.66)], RANGE, false)
    // Bucketed into the :60 slot (the minute it falls in), not dropped for missing :65.
    const slot = grid.find((g) => g.time === 60)
    expect(slot).toEqual({ time: 60, value: 501.66 })
  })

  it('keeps the NEWER of two points sharing the same minute bucket', () => {
    const grid = buildFixedRangeGrid([point(60, 501.5), point(65, 501.66)], RANGE, false)
    const slot = grid.find((g) => g.time === 60)
    expect(slot).toEqual({ time: 60, value: 501.66 })
  })

  it('every minute in range gets a slot — real value or bare whitespace', () => {
    const grid = buildFixedRangeGrid([point(0, 500), point(120, 502)], RANGE, false)
    expect(grid.map((g) => g.time)).toEqual([0, 60, 120])
    expect(grid[1]).toEqual({ time: 60 }) // no point in [60, 120) — whitespace, not dropped
  })

  it("ends at the session's LAST MINUTE, never at its end instant — no slot nothing can fill", () => {
    // A completed session's tape ends in the 19:59 bucket (UBER 2026-09-18: last print
    // 19:59:53, post.end 20:00:00). A slot AT 20:00 is whitespace forever, so the line always
    // stopped one bar short of the pane's edge (owner-reproduced live, 2026-09-21, after the
    // half-bar fit fix: "i still see it though, the gap").
    const grid = buildFixedRangeGrid([point(0, 500), point(173, 502)], RANGE, false)
    expect(grid[grid.length - 1]).toEqual({ time: 120, value: 502 })
  })

  it('still keeps a point stamped exactly AT the session end instant', () => {
    const grid = buildFixedRangeGrid([point(180, 502)], RANGE, false)
    expect(grid[grid.length - 1]).toEqual({ time: 180, value: 502 })
  })

  it('extends past range.to when the tape\'s own last point is later (mid-session "now")', () => {
    const grid = buildFixedRangeGrid([point(245, 503)], RANGE, false)
    expect(grid[grid.length - 1]).toEqual({ time: 240, value: 503 })
  })

  it('never visits a bucket before range.from — a point dated earlier is dropped', () => {
    // The contract that bit the prev-close seed (2026-09-03, KSTL.DE at the XETRA open): the
    // seed was dated at the VIEWER's local midnight, which for most viewers precedes the open,
    // so the grid emitted only the live point and the pane drew nothing. buildIntradayTape now
    // anchors the seed at the session open so it lands in the first slot — see the next case.
    const grid = buildFixedRangeGrid([point(-3600, 499), point(65, 501.66)], RANGE, false)
    expect(grid.filter((g) => 'value' in g)).toEqual([{ time: 60, value: 501.66 }])
  })

  it('draws the prev-close -> live seed when it is anchored at the session open', () => {
    const range = sessionWindowRange({
      regular: { start: '2026-09-03T07:00:00Z', end: '2026-09-03T15:30:00Z' },
    })!
    const seedAt = new Date(range.from * 1000).toISOString()
    const tape = buildIntradayTape(
      [],
      '1D',
      36.56,
      '2026-09-03T07:01:00Z',
      Date.parse('2026-09-03T07:01:30Z'),
      36.75,
      true,
      true,
      seedAt,
    )
    const grid = buildFixedRangeGrid(tape, range, false)
    expect(grid.filter((g) => 'value' in g)).toEqual([
      { time: range.from, value: 36.75 },
      { time: range.from + 60, value: 36.56 },
    ])
  })

  it('carries full OHLC for a candle/bar chart instead of a bare `value`', () => {
    const bar: TailPoint = {
      date: new Date(65_000).toISOString(),
      price: 501.66,
      open: 501.5,
      high: 501.7,
      low: 501.4,
      close: 501.66,
    }
    const grid = buildFixedRangeGrid([bar], RANGE, true)
    const slot = grid.find((g) => g.time === 60)
    expect(slot).toEqual({ time: 60, open: 501.5, high: 501.7, low: 501.4, close: 501.66 })
  })
})

// niceTicks: the custom left-edge axis label values (owner ask 2026-09-21 — no reserved
// right-side gutter, numbers drawn inside the plot "like Perplexity").

describe('niceTicks', () => {
  it('picks a round step (1/2/5 × 10^k) spanning the visible range', () => {
    // ORCL-shaped range — matches the owner's own screenshot: 144.00…154.00 step 2.
    expect(niceTicks(144, 154, 5)).toEqual([144, 146, 148, 150, 152, 154])
  })

  it('scales the step down for a tighter range', () => {
    // RKLB-shaped range — matches the owner's own screenshot: 70.2…71.2 step 0.2.
    const ticks = niceTicks(70.19, 71.25, 5)
    expect(ticks.map((v) => Math.round(v * 10) / 10)).toEqual([70.2, 70.4, 70.6, 70.8, 71.0, 71.2])
  })

  it('never emits more than MAX_AXIS_TICKS labels — steps up to the next nice step instead', () => {
    // MSFT pre-market, owner-reproduced 2026-09-21 ("how to make this less busy"): a 1.5-point
    // range at target 5 picked step 0.2 — nine labels, one every ~30px of a 300px pane. The
    // closest-nice-step rule alone allows up to ~8; the cap steps 0.2 → 0.5, four labels.
    const ticks = niceTicks(493.75, 495.25, 5)
    expect(ticks.map((v) => Math.round(v * 100) / 100)).toEqual([494, 494.5, 495, 495.5])
    const ranges: [number, number][] = [
      [0.9, 2.51],
      [70.19, 71.25],
      [144, 154],
      [1.03, 9.87],
      [999, 1004.4],
    ]
    for (const [lo, hi] of ranges) {
      expect(niceTicks(lo, hi, 5).length).toBeLessThanOrEqual(6)
      expect(niceTicks(lo, hi, 5).length).toBeGreaterThan(1)
    }
  })

  it('never emits a tick outside the requested range plus half a step', () => {
    const ticks = niceTicks(1.03, 9.87, 5)
    expect(ticks.every((v) => v >= 1.03 - 1 && v <= 9.87 + 1)).toBe(true)
    expect(ticks.length).toBeGreaterThan(0)
  })

  it('returns [] for a degenerate, inverted, or non-finite range', () => {
    expect(niceTicks(10, 10)).toEqual([])
    expect(niceTicks(10, 5)).toEqual([])
    expect(niceTicks(NaN, 10)).toEqual([])
    expect(niceTicks(0, Infinity)).toEqual([])
    expect(niceTicks(0, 10, 0)).toEqual([])
  })
})

// volumeAutoscaleCap: the volume pane's soft autoscale ceiling (owner ask 2026-09-21 — a
// closing-auction print routinely dwarfs the rest of the day's per-minute bars, and without
// a cap the pane's autoscale stretches to fit it, squashing every normal bar near zero).

describe('volumeAutoscaleCap', () => {
  it('caps below a single dominant outlier — the 97th percentile of the rest', () => {
    // A realistic 1D bar count (~390 one-minute bars in a regular session) of "normal" volume
    // (100k-1M range) + one closing-auction print at 36M. A handful of points (as few as ~30)
    // isn't enough of a distribution for a 97th-percentile cap to exclude a SINGLE outlier —
    // this needs a count in the real ballpark to exercise the percentile math meaningfully.
    const normal = Array.from({ length: 390 }, (_, i) => 100_000 + i * 2_000)
    const cap = volumeAutoscaleCap([...normal, 36_286_509])
    expect(cap).not.toBeNull()
    expect(cap!).toBeLessThan(2_000_000) // nowhere near the 36M outlier
    expect(cap!).toBeGreaterThanOrEqual(Math.max(...normal) * 0.9) // still near the real top
  })

  it('ignores zero/negative/non-finite values when ranking', () => {
    const cap = volumeAutoscaleCap([0, 0, 100, 200, 300, NaN, -5, Infinity])
    expect(cap).not.toBeNull()
    expect(Number.isFinite(cap)).toBe(true)
  })

  it('returns null with fewer than 3 positive values — not enough of a distribution', () => {
    expect(volumeAutoscaleCap([])).toBeNull()
    expect(volumeAutoscaleCap([100])).toBeNull()
    expect(volumeAutoscaleCap([100, 200])).toBeNull()
    expect(volumeAutoscaleCap([0, 0, 0])).toBeNull()
  })
})

// edgeToEdgeLogicalRange: the fitted 1D range that lands the FIRST and LAST bar's centre on
// the pane's first and last pixel column, so the line runs flush to both edges.

describe('edgeToEdgeLogicalRange', () => {
  // lightweight-charts 5.2's own index → x math (time-scale `indexToCoordinate`), and how
  // `setVisibleLogicalRange` derives spacing/offset from a range — reproduced here so the
  // test proves the geometry, not just the numbers we chose. With `{from: 0, to: N-1}` the
  // last bar sits at `width - barSpacing/2 - 1`: half a slot plus one pixel short of the
  // edge (owner-reproduced live, 2026-09-21: "a 1px empty space between the line and the edge").
  function barX(range: { from: number; to: number }, count: number, width: number) {
    const barSpacing = width / (range.to - range.from + 1)
    const baseIndex = count - 1
    const rightOffset = range.to - baseIndex
    return (index: number) => width - (baseIndex + rightOffset - index + 0.5) * barSpacing - 1
  }

  it('puts the last bar on the last pixel column and the first bar on the first', () => {
    for (const [count, width] of [
      [950, 600],
      [950, 1280],
      [390, 375],
      [2, 100],
    ] as const) {
      const x = barX(edgeToEdgeLogicalRange(count), count, width)
      expect(x(count - 1)).toBeCloseTo(width - 1, 6)
      expect(x(0)).toBeCloseTo(-1, 6)
    }
  })

  it('the naive {0, N-1} pin — the bug — leaves half a bar plus a pixel at the right', () => {
    const x = barX({ from: 0, to: 949 }, 950, 600)
    expect(x(949)).toBeCloseTo(600 - 600 / 950 / 2 - 1, 6)
  })

  it('keeps a degenerate one-bar (or empty) series on a finite range', () => {
    expect(edgeToEdgeLogicalRange(1)).toEqual({ from: 0, to: 0 })
    expect(edgeToEdgeLogicalRange(0)).toEqual({ from: 0, to: 0 })
  })
})

// The two autoscaleInfoProvider bodies. lightweight-charts' `original()` returns
// `{ priceRange: null }` — NOT null — when the visible range holds no valued bar (its
// AutoscaleInfo typing says so), which an all-whitespace 1D grid produces: the first poll
// after a pre-market open, before the first print lands (owner-reproduced live crash,
// 2026-09-21, MSFT 04:01 ET: "Cannot read properties of null (reading 'minValue')").

describe('widenAutoscaleToPrice', () => {
  it('widens the range to include the price', () => {
    expect(widenAutoscaleToPrice({ priceRange: { minValue: 10, maxValue: 12 } }, 15)).toEqual({
      priceRange: { minValue: 10, maxValue: 15 },
    })
    expect(widenAutoscaleToPrice({ priceRange: { minValue: 10, maxValue: 12 } }, 8)).toEqual({
      priceRange: { minValue: 8, maxValue: 12 },
    })
  })

  it('passes a null result or a null priceRange through untouched — never throws', () => {
    expect(widenAutoscaleToPrice(null, 15)).toBeNull()
    expect(widenAutoscaleToPrice({ priceRange: null }, 15)).toEqual({ priceRange: null })
  })

  it('leaves the range alone for a non-finite price', () => {
    const res = { priceRange: { minValue: 10, maxValue: 12 } }
    expect(widenAutoscaleToPrice(res, null)).toEqual(res)
    expect(widenAutoscaleToPrice(res, Number.NaN)).toEqual(res)
  })
})

describe('capAutoscaleMax', () => {
  it('caps the top of the range at the cap (with headroom) and never raises it', () => {
    const capped = capAutoscaleMax({ priceRange: { minValue: 0, maxValue: 1000 } }, 100)
    expect(capped?.priceRange?.maxValue).toBeCloseTo(115, 9)
    expect(capAutoscaleMax({ priceRange: { minValue: 0, maxValue: 50 } }, 100)).toEqual({
      priceRange: { minValue: 0, maxValue: 50 },
    })
  })

  it('passes a null result, a null priceRange, or a null cap through — never throws', () => {
    expect(capAutoscaleMax(null, 100)).toBeNull()
    expect(capAutoscaleMax({ priceRange: null }, 100)).toEqual({ priceRange: null })
    const res = { priceRange: { minValue: 0, maxValue: 1000 } }
    expect(capAutoscaleMax(res, null)).toEqual(res)
  })
})

// usableSessionWindow: the fixed 1D window is only usable when the tape it's about to grid
// actually belongs to it. For the minutes after a pre-market open the cached chart meta can
// still describe the PREVIOUS session while the 1-minute tape and the live print are
// today's (owner-reproduced live, 2026-09-21, MSFT 04:02 ET): gridding Monday's first print
// onto Friday's window drew 66 hours of whitespace with "pre-market on the right side
// instead of left". Such bounds are refused so the chart falls back to the plain fit until
// they catch up.

describe('usableSessionWindow', () => {
  const FRI = {
    pre: { start: '2026-09-18T08:00:00Z', end: '2026-09-18T13:30:00Z' },
    regular: { start: '2026-09-18T13:30:00Z', end: '2026-09-18T20:00:00Z' },
    post: { start: '2026-09-18T20:00:00Z', end: '2026-09-19T00:00:00Z' },
  }
  const at = (iso: string, price = 1): TailPoint => ({ date: iso, price })

  it('is the plain session window when the tape ends inside it (or within the hour after)', () => {
    const range = sessionWindowRange(FRI)!
    expect(usableSessionWindow(FRI, [at('2026-09-18T19:59:53Z')], null)).toEqual(range)
    // The live print a few seconds past post.end is the ordinary end-of-day shape.
    expect(usableSessionWindow(FRI, [at('2026-09-18T23:59:00Z')], '2026-09-19T00:00:07Z')).toEqual(
      range,
    )
  })

  it('refuses bounds the tape has run more than an hour past — the previous session', () => {
    // Monday 04:02 ET's first pre-market bar, gridded on Friday's (stale) bounds.
    expect(usableSessionWindow(FRI, [at('2026-09-21T08:02:00Z')], null)).toBeNull()
    // The same with only the LIVE print past the window.
    expect(
      usableSessionWindow(FRI, [at('2026-09-18T19:59:00Z')], '2026-09-21T08:02:25Z'),
    ).toBeNull()
  })

  it('is null without bounds, and the window itself with no tape to judge it by', () => {
    expect(usableSessionWindow(null, [at('2026-09-18T19:59:00Z')], null)).toBeNull()
    expect(usableSessionWindow(FRI, [], null)).toEqual(sessionWindowRange(FRI))
  })
})

// tickMarkFormatterFor: every time-axis label is padded with the SAME whitespace on both
// sides. lightweight-charts clamps an overflowing edge label to exactly [0, width] using the
// MEASURED string and never trims it, while tick density comes from a character-count
// estimate — so the padding moves only the labels that were touching the border (they end
// up inset by the pad) and nothing else: centred labels keep their centre, tick selection is
// unchanged (owner ask 2026-09-21: "padding only for the time text, not the whole chart").

describe('tickMarkFormatterFor', () => {
  const fmt = tickMarkFormatterFor('America/New_York', () => true)
  const t = (iso: string) => (Date.parse(iso) / 1000) as UTCTimestamp

  it('pads every label symmetrically with the edge pad', () => {
    const label = fmt(t('2026-09-21T13:30:00Z'), TickMarkType.Time)
    expect(label.startsWith(AXIS_LABEL_EDGE_PAD)).toBe(true)
    expect(label.endsWith(AXIS_LABEL_EDGE_PAD)).toBe(true)
    expect(label.slice(AXIS_LABEL_EDGE_PAD.length, -AXIS_LABEL_EDGE_PAD.length)).toBe('09:30 AM')
  })

  it('pads date labels the same way on the daily timeframes', () => {
    const daily = tickMarkFormatterFor('America/New_York', () => false)
    const label = daily(t('2026-09-01T13:30:00Z'), TickMarkType.Month)
    expect(label).toBe(`${AXIS_LABEL_EDGE_PAD}Sep '26${AXIS_LABEL_EDGE_PAD}`)
  })

  it('the pad is whitespace only — nothing visible is ever added to a label', () => {
    expect(AXIS_LABEL_EDGE_PAD.trim()).toBe('')
    expect(AXIS_LABEL_EDGE_PAD.length).toBeGreaterThan(0)
  })
})
