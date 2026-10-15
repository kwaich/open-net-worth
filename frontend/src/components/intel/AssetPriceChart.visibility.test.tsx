// @vitest-environment jsdom
//
// Regression: the 1D chart's 60s poll only checked `document.hidden` INSIDE the interval
// callback — it never caught up on `visibilitychange`. If a fetch landed empty/failed while
// the tab was hidden (or the tab was backgrounded across a tick), the chart stayed stuck on
// that stale result until the next natural 60s tick happened to fire while visible, which can
// be a long wait. useAssetDetail.ts's own poll already registers a `visibilitychange` listener
// for exactly this reason (see its "catches up the moment the tab is shown again" comment) —
// this test pins the same behavior for AssetPriceChart's 1D poll.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act } from '@testing-library/react'
import { AssetPriceChart } from './AssetPriceChart'
import type { AssetPriceSeries } from '../../types/assetDetail'

vi.mock('../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function makeSeries(points: number): AssetPriceSeries {
  return {
    price_source_id: 'ONWAI.DE',
    currency: 'EUR',
    intraday_available: true,
    ohlc_available: false,
    points: Array.from({ length: points }, (_, i) => ({
      date: `2026-07-06T${10 + i}:00:00`,
      price: 37 + i * 0.1,
    })),
  }
}

// jsdom has no real canvas 2D context. The old Chart.js chart tolerated `getContext`
// returning undefined (it never drew synchronously in this test's timeframe), but
// lightweight-charts measures text and draws synchronously at chart-creation time —
// it needs every 2D context method to at least exist and not throw. A Proxy fakes an
// arbitrarily-complete context: any method call is a no-op, any other property read
// returns undefined, and `canvas` resolves back to the real element (lightweight-charts
// reads its dimensions off `ctx.canvas`).
function fakeCanvasContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const store: Record<string, unknown> = { canvas }
  return new Proxy(store, {
    get(target, prop: string) {
      if (prop in target) return target[prop]
      if (prop === 'measureText') return () => ({ width: 8 })
      if (prop === 'createLinearGradient' || prop === 'createRadialGradient') {
        return () => ({ addColorStop: () => {} })
      }
      if (prop === 'getTransform') return () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 })
      if (prop === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) })
      // Any other canvas method (save/restore/fillRect/beginPath/stroke/...) is a no-op.
      return () => undefined
    },
    set(target, prop: string, value) {
      target[prop] = value
      return true
    },
  }) as unknown as CanvasRenderingContext2D
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  HTMLCanvasElement.prototype.getContext = function (this: HTMLCanvasElement) {
    return fakeCanvasContext(this)
  } as unknown as typeof HTMLCanvasElement.prototype.getContext
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('AssetPriceChart — 1D poll catches up on visibilitychange', () => {
  it('re-fetches immediately when the tab becomes visible again, not waiting for the next 60s tick', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(makeSeries(2)))

    render(<AssetPriceChart instrumentId="a1" currency="EUR" marketOpen />)
    await act(async () => {})
    const callsAtMount = mockAuthFetch.mock.calls.length

    // The tab goes hidden — no poll fires while hidden.
    const hiddenSpy = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockAuthFetch.mock.calls.length).toBe(callsAtMount)

    // Fresher data is now available server-side (e.g. a transient failure resolved).
    mockAuthFetch.mockResolvedValue(okJson(makeSeries(5)))

    // Tab becomes visible again — well before the next scheduled 60s tick.
    hiddenSpy.mockReturnValue(false)
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
      await Promise.resolve()
    })

    expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(callsAtMount)
    hiddenSpy.mockRestore()
  })

  it('still polls normally on the 60s tick while visible (unchanged baseline behavior)', async () => {
    vi.useFakeTimers()
    mockAuthFetch.mockResolvedValue(okJson(makeSeries(2)))

    render(<AssetPriceChart instrumentId="a2" currency="EUR" marketOpen />)
    await act(async () => {})
    const callsAtMount = mockAuthFetch.mock.calls.length

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockAuthFetch.mock.calls.length).toBeGreaterThan(callsAtMount)
  })
})
