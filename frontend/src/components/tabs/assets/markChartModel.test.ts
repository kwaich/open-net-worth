// Pure domain-math tests for the value chart — no canvas, no Chart.js instance.
import { describe, expect, it } from 'vitest'
import type { AssetMark, OwnedAsset } from '../../../types/assets'
import { markChartModel, toYear, withPinnedTick, yearTicks } from './markChartModel'

function mark(overrides: Partial<AssetMark>): AssetMark {
  return {
    id: 'mk1',
    kind: 'valuation',
    date: '2026-01-01',
    value: 1000,
    note: '',
    by: 'you',
    created_at: '2026-01-01T00:00:00Z',
    edited_at: null,
    ...overrides,
  }
}

function asset(overrides: Partial<OwnedAsset> = {}): OwnedAsset {
  return {
    id: 'oa_1',
    name: 'Test asset',
    category: 'other',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 1000,
    valued_on: '2026-01-01',
    share_value: 1000,
    marks: [mark({})],
    docs: [],
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

/** A fixed "today" — the axis ends there. */
const TODAY = '2026-09-26'

describe('toYear', () => {
  it('parses an ISO date into a fractional year', () => {
    expect(toYear('2026-01-01')).toBeCloseTo(2026, 5)
    // April (month index 3) 15th: 2026 + 3/12 + 14/365
    expect(toYear('2026-04-15')).toBeCloseTo(2026 + 3 / 12 + 14 / 365, 5)
  })
})

describe('markChartModel — single-mark span', () => {
  it('gives a lone mark a year of span to draw against, ending today', () => {
    const a = asset({ marks: [mark({ id: 'm1', date: '2026-01-01', value: 1000 })] })
    const model = markChartModel(a, TODAY)
    expect(model.xMax).toBeCloseTo(toYear(TODAY), 5)
    expect(model.x0).toBeLessThanOrEqual(toYear('2026-01-01'))
    expect(model.xMax - model.x0).toBeGreaterThanOrEqual(1 - 1e-9)
    expect(model.single).toBe(true)
  })

  it('never labels a future year — the right edge is this year', () => {
    const a = asset({ marks: [mark({ id: 'm1', date: TODAY, value: 1000 })] })
    const model = markChartModel(a, TODAY)
    const years = yearTicks(model.x0, model.xMax).map((t) => Math.floor(t))
    expect(Math.max(...years)).toBe(2026)
  })

  it('holds the single mark forward as the last series point', () => {
    const a = asset({ marks: [mark({ id: 'm1', date: '2026-01-01', value: 1000 })] })
    const model = markChartModel(a, TODAY)
    expect(model.series).toHaveLength(2) // the real point + the hold-forward point
    const holdForward = model.series[model.series.length - 1]!
    expect(holdForward.i).toBe(-1)
    expect(holdForward.y).toBe(1000)
    expect(holdForward.x).toBeCloseTo(model.xMax, 5)
  })
})

describe('markChartModel — held forward from the current value', () => {
  it("holds the asset's value forward, never whichever point sorts first", () => {
    // A same-day purchase point listed first must not become the held-forward value.
    const a = asset({
      value: 1300,
      cost: 900,
      cost_date: '2026-06-01',
      marks: [
        mark({ id: 'purchase', kind: 'purchase', date: '2026-06-01', value: 900 }),
        mark({ id: 'm1', date: '2026-06-01', value: 1300 }),
      ],
    })
    const model = markChartModel(a, TODAY)
    expect(model.series[model.series.length - 1]).toMatchObject({ i: -1, y: 1300 })
  })

  it('draws no held-forward point for an asset with no value yet', () => {
    const a = asset({ value: null, valued_on: null, share_value: null, marks: [mark({})] })
    const model = markChartModel(a, TODAY)
    expect(model.series).toHaveLength(1)
    expect(model.series.every((p) => p.i >= 0)).toBe(true)
  })
})

describe('markChartModel — multi-mark domain', () => {
  it('spans the oldest to the newest mark, oldest first', () => {
    // marks arrive newest-first from the API
    const a = asset({
      marks: [
        mark({ id: 'm3', date: '2026-06-01', value: 1300 }),
        mark({ id: 'm2', date: '2025-06-01', value: 1200 }),
        mark({ id: 'm1', date: '2024-06-01', value: 1000 }),
      ],
    })
    const model = markChartModel(a, TODAY)
    expect(model.marksAsc.map((m) => m.id)).toEqual(['m1', 'm2', 'm3'])
    expect(model.x0).toBeCloseTo(toYear('2024-06-01'), 5)
    expect(model.xMax).toBeCloseTo(toYear(TODAY), 5)
    expect(model.single).toBe(false)
    // three real points, plus the hold-forward point
    expect(model.series).toHaveLength(4)
    expect(model.series.slice(0, 3).map((p) => p.y)).toEqual([1000, 1200, 1300])
  })

  it('pads the y domain around the marks', () => {
    const a = asset({
      marks: [
        mark({ id: 'm2', date: '2026-01-01', value: 1500 }),
        mark({ id: 'm1', date: '2020-01-01', value: 500 }),
      ],
    })
    const model = markChartModel(a, TODAY)
    expect(model.yMin).toBeLessThan(500)
    expect(model.yMin).toBeGreaterThanOrEqual(0)
    expect(model.yMax).toBeGreaterThan(1500)
  })
})

describe('markChartModel — cost line', () => {
  it('is present when a purchase price is recorded', () => {
    const a = asset({ cost: 800, marks: [mark({ value: 1000 })] })
    const model = markChartModel(a, TODAY)
    expect(model.costLine).not.toBeNull()
    expect(model.costLine!.value).toBe(800)
    expect(model.costLine!.x0).toBeCloseTo(model.x0, 5)
    expect(model.costLine!.xMax).toBeCloseTo(model.xMax, 5)
  })

  it('is absent, and the color neutral, when there is no purchase price', () => {
    const a = asset({ cost: null, marks: [mark({ value: 1000 })] })
    const model = markChartModel(a, TODAY)
    expect(model.costLine).toBeNull()
    expect(model.colorKey).toBe('neutral')
  })
})

describe('markChartModel — color vs. purchase price', () => {
  it('is "good" when the current value is at or above cost', () => {
    const a = asset({ cost: 800, value: 1000, marks: [mark({ value: 1000 })] })
    expect(markChartModel(a, TODAY).colorKey).toBe('good')
  })

  it('is "bad" when the current value is below cost', () => {
    const a = asset({ cost: 1200, value: 1000, marks: [mark({ value: 1000 })] })
    expect(markChartModel(a, TODAY).colorKey).toBe('bad')
  })
})

describe('yearTicks', () => {
  it('keeps the edge ticks at the EXACT fractional x0/xMax, floored years in between', () => {
    // Oct 2024 → x0 ≈ 2024.75; Chart.js's own "nice number" auto-ticker would only offer
    // 2025/2026/2027 here, since a tick AT 2024 sits before x0 — the real start year never
    // got a label, reading as if tracking began a year later than it did. The edges stay
    // fractional (not floored/ceiled to 2024/2028) so their pixel position is exactly the
    // plot's own bound — a value outside [x0, xMax] risks Chart.js re-expanding (or
    // mis-clipping) the whole rendered range; only the DISPLAYED year (the chart's own
    // ticks.callback, Math.floor) needs rounding, never the tick's position.
    const x0 = toYear('2024-10-01')
    const xMax = toYear('2026-09-01') + 0.35
    const ticks = yearTicks(x0, xMax)
    expect(ticks[0]).toBeCloseTo(x0, 5)
    expect(ticks[ticks.length - 1]).toBeCloseTo(xMax, 5)
    // 2025 is dropped — barely a quarter-year after x0 (2024.75), it would render right on
    // top of the "2024" edge label.
    expect(ticks.map((t) => Math.floor(t))).toEqual([2024, 2026, 2027])
  })

  it("drops a neighbor tick less than half a year from an edge, so it can't crowd the edge's own label", () => {
    const ticks = yearTicks(2024.9, 2027.1)
    const years = ticks.map((t) => Math.floor(t))
    expect(years).toEqual([2024, 2026, 2027])
  })

  it('is just the one point for a mark with no real span', () => {
    expect(yearTicks(2026, 2026)).toEqual([2026])
  })

  it('thins evenly to at most `max` ticks over a long span', () => {
    const ticks = yearTicks(2000, 2026, 6)
    expect(ticks.length).toBeLessThanOrEqual(6)
    expect(ticks[0]).toBe(2000)
    expect(ticks[ticks.length - 1]).toBe(2026)
  })
})

describe('withPinnedTick', () => {
  it('inserts the pinned value in sorted order among the existing ticks', () => {
    expect(withPinnedTick([18850, 20000, 22000, 24150], 23000)).toEqual([
      18850, 20000, 22000, 23000, 24150,
    ])
  })

  it('drops an existing tick close enough to crowd the pinned value', () => {
    // avg step ≈ 1767; 23800 is only 350 from 24150 — well within the 40% (≈707) threshold
    const ticks = withPinnedTick([18850, 20000, 22000, 24150], 23800)
    expect(ticks).not.toContain(24150)
    expect(ticks).toContain(23800)
  })

  it('just adds the value in order when there are fewer than 2 existing ticks', () => {
    expect(withPinnedTick([20000], 23000)).toEqual([20000, 23000])
    expect(withPinnedTick([], 23000)).toEqual([23000])
  })
})
