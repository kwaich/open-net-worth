import { describe, expect, it } from 'vitest'
import { gapRuns } from './sparklineGap'

describe('gapRuns', () => {
  it('is one "ahead" run when the line never dips below the comparison', () => {
    const runs = gapRuns([10, 12, 15], [10, 11, 12])
    expect(runs).toHaveLength(1)
    expect(runs[0]!.ahead).toBe(true)
    expect(runs[0]!.pts.map((p) => p.x)).toEqual([0, 1, 2])
  })

  it('is one "behind" run when the line stays under it', () => {
    const runs = gapRuns([9, 8, 7], [10, 10, 10])
    expect(runs.map((r) => r.ahead)).toEqual([false])
  })

  it('splits at the interpolated crossing, which both runs share', () => {
    // diff goes +2 → −2 between index 1 and 2: they meet halfway, at 11.
    const runs = gapRuns([10, 12, 10], [10, 10, 12])
    expect(runs.map((r) => r.ahead)).toEqual([true, false])
    const cross = runs[0]!.pts[runs[0]!.pts.length - 1]!
    expect(cross).toEqual({ x: 1.5, v: 11, c: 11 })
    expect(runs[1]!.pts[0]).toEqual(cross)
  })
})

describe('gapRuns — touching points', () => {
  it('gives the shared starting point the side of the first gap, so no empty run leads', () => {
    const runs = gapRuns([120, 118, 100], [120, 121, 122])
    expect(runs.map((r) => r.ahead)).toEqual([false])
    expect(runs[0]!.pts).toHaveLength(3)
  })
})

describe('gapRuns — edges', () => {
  it('is one run when the lines never part', () => {
    const runs = gapRuns([5, 5, 5], [5, 5, 5])
    expect(runs).toHaveLength(1)
    expect(runs[0]!.pts.map((p) => p.x)).toEqual([0, 1, 2])
  })

  it('gives a touch on the last point the side before it', () => {
    const runs = gapRuns([10, 12, 11], [10, 11, 11])
    expect(runs.map((r) => r.ahead)).toEqual([true])
    expect(runs[0]!.pts).toHaveLength(3)
  })

  it('reads only as far as the shorter series', () => {
    const runs = gapRuns([10, 12, 14, 16], [10, 11])
    expect(runs[0]!.pts.map((p) => p.x)).toEqual([0, 1])
  })
})
