// @vitest-environment node
import { describe, it, expect } from 'vitest'
import {
  DISMISS_DISTANCE_RATIO,
  DISMISS_VELOCITY_PX_MS,
  RUBBER_BAND_MAX_PX,
  SCRIM_OPACITY_FLOOR_RATIO,
  clamp,
  computeVelocity,
  dragOffset,
  dragProgress,
  scrimOpacity,
  shouldCommitDismiss,
  type PointerSample,
} from './sheetDragPhysics'

describe('clamp', () => {
  it('passes values already inside the range through unchanged', () => {
    expect(clamp(5, 0, 10)).toBe(5)
  })
  it('clamps below the minimum', () => {
    expect(clamp(-5, 0, 10)).toBe(0)
  })
  it('clamps above the maximum', () => {
    expect(clamp(15, 0, 10)).toBe(10)
  })
})

describe('dragOffset (rubber-band damping)', () => {
  it('tracks 1:1 while pulling down', () => {
    expect(dragOffset(40)).toBe(40)
    expect(dragOffset(0)).toBe(0)
  })
  it('damps an upward pull, staying at or under the cap', () => {
    // An extreme delta underflows the exponential term to exactly 0 in floating point, so the
    // asymptote is only checked as <=, not strictly < — the moderate-pull case below is what
    // actually proves the curve is damping rather than clamping.
    const offset = dragOffset(-1000, RUBBER_BAND_MAX_PX)
    expect(offset).toBeLessThan(0)
    expect(Math.abs(offset)).toBeLessThanOrEqual(RUBBER_BAND_MAX_PX)
  })
  it('a small upward pull damps to noticeably less than the raw delta', () => {
    const offset = dragOffset(-20, RUBBER_BAND_MAX_PX)
    expect(offset).toBeLessThan(0)
    expect(Math.abs(offset)).toBeLessThan(20)
  })
  it('is monotonic: a bigger upward pull never produces a smaller visual offset', () => {
    const small = Math.abs(dragOffset(-10, RUBBER_BAND_MAX_PX))
    const big = Math.abs(dragOffset(-100, RUBBER_BAND_MAX_PX))
    expect(big).toBeGreaterThan(small)
  })
})

describe('dragProgress', () => {
  it('0 at rest, 1 once pulled a full sheet-height down', () => {
    expect(dragProgress(0, 400)).toBe(0)
    expect(dragProgress(400, 400)).toBe(1)
  })
  it('clamps past a full sheet-height to 1, never overshoots', () => {
    expect(dragProgress(4000, 400)).toBe(1)
  })
  it('an upward pull (negative delta) never contributes negative progress', () => {
    expect(dragProgress(-50, 400)).toBe(0)
  })
  it('a zero-height sheet never divides by zero', () => {
    expect(dragProgress(100, 0)).toBe(0)
  })
})

describe('scrimOpacity', () => {
  it('rest opacity at zero progress', () => {
    expect(scrimOpacity(0, 1)).toBe(1)
  })
  it('floors at the configured ratio of rest opacity, never exactly 0', () => {
    const atFullDrag = scrimOpacity(1, 1, SCRIM_OPACITY_FLOOR_RATIO)
    expect(atFullDrag).toBeCloseTo(SCRIM_OPACITY_FLOOR_RATIO)
    expect(atFullDrag).toBeGreaterThan(0)
  })
  it('interpolates monotonically between rest and the floor', () => {
    const quarter = scrimOpacity(0.25, 1)
    const half = scrimOpacity(0.5, 1)
    expect(quarter).toBeGreaterThan(half)
    expect(half).toBeGreaterThan(scrimOpacity(1, 1))
  })
  it('scales with a non-default rest opacity', () => {
    expect(scrimOpacity(0, 0.6)).toBeCloseTo(0.6)
  })
})

describe('computeVelocity', () => {
  const sample = (t: number, y: number): PointerSample => ({ t, y })

  it('0 with fewer than two samples', () => {
    expect(computeVelocity([])).toBe(0)
    expect(computeVelocity([sample(0, 0)])).toBe(0)
  })
  it('px/ms, positive for downward movement', () => {
    const v = computeVelocity([sample(0, 0), sample(100, 50)])
    expect(v).toBeCloseTo(0.5)
  })
  it('negative for upward movement', () => {
    const v = computeVelocity([sample(0, 50), sample(100, 0)])
    expect(v).toBeCloseTo(-0.5)
  })
  it('only weighs samples inside the trailing window — a slow start does not dilute a late flick', () => {
    const samples = [sample(0, 0), sample(500, 10), sample(560, 70)]
    // Whole-gesture average would be ~0.125 px/ms; the last 80ms (500→560, 10→70) is 1 px/ms.
    expect(computeVelocity(samples, 80)).toBeCloseTo(1)
  })
  it('0 when the trailing samples share a timestamp (dt === 0)', () => {
    expect(computeVelocity([sample(0, 0), sample(0, 40)])).toBe(0)
  })
  it('falls back to the oldest prior sample when every one is outside the trailing window — a throttled drag whose samples arrive sparser than windowMs must not silently read as 0', () => {
    // latest.t=1000, cutoff = 1000-80=920; the only prior sample (t=0) is nowhere near it, so
    // there is no PRIOR sample >= cutoff and computeVelocity must fall back to samples[0]
    // rather than comparing latest against itself.
    const v = computeVelocity([sample(0, 0), sample(1000, 500)], 80)
    expect(v).toBeCloseTo(0.5)
  })
})

describe('shouldCommitDismiss', () => {
  it('commits once the drag distance crosses the ratio, even at zero velocity', () => {
    expect(shouldCommitDismiss({ offsetY: 200, sheetHeight: 400, velocity: 0 })).toBe(true)
  })
  it('does not commit below the distance ratio at zero velocity', () => {
    expect(shouldCommitDismiss({ offsetY: 50, sheetHeight: 400, velocity: 0 })).toBe(false)
  })
  it('commits on a fast flick even well under the distance ratio', () => {
    expect(shouldCommitDismiss({ offsetY: 20, sheetHeight: 400, velocity: 1.2 })).toBe(true)
  })
  it('never commits an upward or zero drag, regardless of velocity', () => {
    expect(shouldCommitDismiss({ offsetY: 0, sheetHeight: 400, velocity: 5 })).toBe(false)
    expect(shouldCommitDismiss({ offsetY: -20, sheetHeight: 400, velocity: 5 })).toBe(false)
  })
  it('respects custom thresholds', () => {
    expect(
      shouldCommitDismiss({
        offsetY: 100,
        sheetHeight: 400,
        velocity: 0,
        distanceRatio: 0.2,
      }),
    ).toBe(true)
    expect(
      shouldCommitDismiss({
        offsetY: 20,
        sheetHeight: 400,
        velocity: 0.3,
        velocityThreshold: 0.1,
      }),
    ).toBe(true)
  })
  it('the exported defaults match the documented contract', () => {
    expect(DISMISS_DISTANCE_RATIO).toBe(0.35)
    expect(DISMISS_VELOCITY_PX_MS).toBe(0.5)
  })
})
