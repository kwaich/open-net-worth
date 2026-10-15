// @vitest-environment jsdom
//
// Fullscreen chart mode: the expand button moves the SAME chart card (same component
// state) into a workspace Modal where pan/zoom is enabled; Escape / × closes it and
// the card returns inline. Uses the visibility test's fake-canvas harness — jsdom has
// no 2D context and lightweight-charts draws synchronously at creation.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, act, fireEvent, screen } from '@testing-library/react'
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
    ohlc_available: true,
    points: Array.from({ length: points }, (_, i) => ({
      date: `2026-07-06T${10 + i}:00:00`,
      price: 37 + i * 0.1,
      open: 37,
      high: 38,
      low: 36.5,
      close: 37 + i * 0.1,
      volume: 100 + i,
    })),
  }
}

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
})

describe('AssetPriceChart — fullscreen mode', () => {
  it('expand moves the card into a workspace modal; Escape returns it inline', async () => {
    mockAuthFetch.mockResolvedValue(okJson(makeSeries(3)))
    const { container } = render(<AssetPriceChart instrumentId="a1" currency="EUR" marketOpen />)
    await act(async () => {})

    // Inline: card present, no modal, expand affordance offered.
    expect(container.querySelector('.asd-chart-wrap')).toBeTruthy()
    expect(document.body.querySelector('.modal-overlay')).toBeNull()
    const expand = screen.getByRole('button', { name: /expand chart/i })

    await act(async () => {
      fireEvent.click(expand)
    })

    // Fullscreen: the card now lives inside the modal's flexing body, with the chart
    // host remounted there (the rebuild picks up pan/zoom — covered by the
    // buildChartOptions unit tests); the expand button is gone (Modal's × owns close).
    expect(document.body.querySelector('.modal-overlay')).toBeTruthy()
    expect(document.body.querySelector('.asd-chart-fs .asd-chart-wrap')).toBeTruthy()
    expect(document.body.querySelector('.asd-chart-fs .asd-chart-lw')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /expand chart/i })).toBeNull()

    await act(async () => {
      fireEvent.keyDown(document, { key: 'Escape' })
    })

    // Back inline: modal gone, card re-mounted in place.
    expect(document.body.querySelector('.modal-overlay')).toBeNull()
    expect(container.querySelector('.asd-chart-wrap')).toBeTruthy()
    expect(screen.getByRole('button', { name: /expand chart/i })).toBeTruthy()
  })

  it('keeps the selected timeframe across the fullscreen round-trip (state lives in the component)', async () => {
    mockAuthFetch.mockResolvedValue(okJson(makeSeries(3)))
    const { container } = render(<AssetPriceChart instrumentId="a1" currency="EUR" marketOpen />)
    await act(async () => {})

    // Switch to 1M inline (segmented control), then round-trip fullscreen.
    await act(async () => {
      fireEvent.click(screen.getByRole('tab', { name: '1M' }))
    })
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /expand chart/i }))
    })
    // The fullscreen toolbar reflects the same selection (aria-pressed / active seg).
    const seg = document.body.querySelector('.asd-chart-fs .ui-segmented__seg--active')
    expect(seg?.textContent).toBe('1M')

    await act(async () => {
      fireEvent.keyDown(document, { key: 'Escape' })
    })
    const inlineSeg = container.querySelector('.ui-segmented__seg--active')
    expect(inlineSeg?.textContent).toBe('1M')
  })
})
