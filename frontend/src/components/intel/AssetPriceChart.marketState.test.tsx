// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AssetPriceChart } from './AssetPriceChart'
import type { AssetPriceSeries } from '../../types/assetDetail'

vi.mock('../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../api/client'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function series(overrides: Partial<AssetPriceSeries> = {}): AssetPriceSeries {
  return {
    price_source_id: 'bitcoin',
    currency: 'USD',
    intraday_available: true,
    ohlc_available: true,
    points: [
      { date: '2026-08-12T10:00:00Z', price: 118000 },
      { date: '2026-08-12T11:00:00Z', price: 118500 },
    ],
    ...overrides,
  }
}

function fakeCanvasContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const store: Record<string, unknown> = { canvas }
  return new Proxy(store, {
    get(target, prop: string) {
      if (prop in target) return target[prop]
      if (prop === 'measureText') return () => ({ width: 8 })
      if (prop === 'createLinearGradient' || prop === 'createRadialGradient')
        return () => ({ addColorStop: () => {} })
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
  vi.useRealTimers()
})

describe('AssetPriceChart market-data state', () => {
  it('announces async market-state transitions through one persistent polite status region', async () => {
    const request = deferred<Response>()
    mockAuthFetch.mockImplementationOnce(() => request.promise)

    render(<AssetPriceChart instrumentId="btc-announcement" currency="USD" assetClass="crypto" />)

    const status = screen.getByRole('status')
    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(status.getAttribute('aria-live')).toBe('polite')
    expect(status.getAttribute('aria-atomic')).toBe('true')
    expect(status.textContent).toContain('Price history is loading')

    await act(async () => {
      request.resolve(
        okJson(series({ provider: 'yahoo', state: 'stale', as_of: '2026-08-12T09:00:00Z' })),
      )
      await request.promise
    })

    expect(screen.getAllByRole('status')).toHaveLength(1)
    expect(screen.getByRole('status')).toBe(status)
    expect(status.textContent).toContain('saved Yahoo price history')
    expect(status.textContent).toContain('live providers are unavailable')
  })

  it('renders an explicit unavailable response without a chart host and retries only the series', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson(
        series({ points: [], intraday_available: false, provider: null, state: 'unavailable' }),
      ),
    )

    const { container } = render(
      <AssetPriceChart instrumentId="btc" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})

    expect(screen.getByText('Price history unavailable')).toBeTruthy()
    expect(
      screen.getByText(
        'Live market data is unavailable and there is no recent saved history for this timeframe.',
      ),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/intel/assets/btc/series?timeframe=1D&vs=USD')
      expect(mockAuthFetch).toHaveBeenCalledTimes(2)
    })
    expect(container.querySelector('.asd-chart-lw')).toBeNull()
    expect(container.querySelector('canvas')).toBeNull()
  })

  it('draws the prev-close -> live seed at market open instead of the hard error, even when the backend reports unavailable', async () => {
    // Reproduces KSTL.DE at the XETRA open: the delayed venue has printed zero intraday bars
    // yet (server correctly reports state: 'unavailable'), but the header already has both a
    // live quote and yesterday's close — enough for buildIntradayTape's synthetic seed to draw
    // a 2-point line. That seed must win over the hard "Price history unavailable" error. The
    // seed draws silently (no "Delayed feed" banner — owner ask 2026-09-04: it read as clutter
    // for the routine first couple of minutes after an open).
    // `sessionBounds` is what the real caller always passes for an open venue — it is what
    // routes the tape through the fixed 1-minute grid that dropped the old local-midnight seed.
    mockAuthFetch.mockResolvedValue(
      okJson(
        series({ points: [], intraday_available: false, provider: null, state: 'unavailable' }),
      ),
    )

    const { container } = render(
      <AssetPriceChart
        instrumentId="kstl-open"
        currency="USD"
        assetClass="equity"
        marketOpen
        prevClose={36.75}
        livePrice={36.56}
        liveAt="2026-09-03T07:01:00Z"
        sessionBounds={{
          regular: { start: '2026-09-03T07:00:00Z', end: '2026-09-03T15:30:00Z' },
        }}
        timezone="Europe/Berlin"
      />,
    )
    await act(async () => {})

    expect(screen.queryByText('Price history unavailable')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(container.querySelector('.asd-chart-lw')).toBeTruthy()
    expect(screen.queryByText('Delayed feed')).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('')
  })

  it('draws a stale series with no visual banner', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson(series({ provider: 'yahoo', state: 'stale', as_of: '2026-08-12T09:00:00Z' })),
    )

    const { container } = render(
      <AssetPriceChart instrumentId="btc-stale" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})

    expect(screen.queryByText(/Saved data/)).toBeNull()
    expect(screen.queryByText('Live providers are temporarily unavailable.')).toBeNull()
    expect(container.querySelector('.asd-market-stale')).toBeNull()
    expect(container.querySelector('.asd-chart-lw')).toBeTruthy()
  })

  it('does not promote healthy market data into a standalone chart status bar', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson(series({ provider: 'coingecko', state: 'live', as_of: '2026-08-12T11:00:00Z' })),
    )

    const { container } = render(
      <AssetPriceChart instrumentId="btc-live" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})

    expect(container.querySelector('.asd-market-status')).toBeNull()
    expect(screen.queryByText(/Live fallback/)).toBeNull()
    expect(screen.queryByText('Live providers are temporarily unavailable.')).toBeNull()
  })

  it('keeps a newer drawable cache entry when an older revalidation arrives', async () => {
    vi.useFakeTimers()
    mockAuthFetch
      .mockResolvedValueOnce(
        okJson(series({ provider: 'yahoo', state: 'live', as_of: '2026-08-12T12:00:00Z' })),
      )
      .mockResolvedValueOnce(
        okJson(series({ provider: 'coingecko', state: 'live', as_of: '2026-08-12T11:00:00Z' })),
      )
      .mockImplementation(() => new Promise<Response>(() => {}))

    const first = render(
      <AssetPriceChart instrumentId="cache-newer" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(screen.getByRole('status').textContent).toContain('fresh Yahoo market data')
    first.unmount()

    render(<AssetPriceChart instrumentId="cache-newer" currency="USD" assetClass="crypto" />)
    expect(screen.getByRole('status').textContent).toContain('fresh Yahoo market data')
    vi.useRealTimers()
  })

  it('removes an explicit unavailable response from the cache instead of reviving an expired chart', async () => {
    vi.useFakeTimers()
    mockAuthFetch
      .mockResolvedValueOnce(
        okJson(series({ provider: 'yahoo', state: 'live', as_of: '2026-08-12T12:00:00Z' })),
      )
      .mockResolvedValueOnce(
        okJson(
          series({ points: [], intraday_available: false, provider: null, state: 'unavailable' }),
        ),
      )
      .mockImplementation(() => new Promise<Response>(() => {}))

    const first = render(
      <AssetPriceChart instrumentId="cache-unavailable" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    first.unmount()

    const second = render(
      <AssetPriceChart instrumentId="cache-unavailable" currency="USD" assetClass="crypto" />,
    )
    expect(second.container.querySelector('.asd-chart-lw')).toBeNull()
    vi.useRealTimers()
  })

  it('keeps a newer unavailable poll authoritative when an older live request resolves last', async () => {
    vi.useFakeTimers()
    const firstRequest = deferred<Response>()
    const newerRequest = deferred<Response>()
    mockAuthFetch
      .mockImplementationOnce(() => firstRequest.promise)
      .mockImplementationOnce(() => newerRequest.promise)
      .mockImplementation(() => new Promise<Response>(() => {}))

    const first = render(
      <AssetPriceChart instrumentId="overlap-unavailable" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)

    await act(async () => {
      newerRequest.resolve(
        okJson(
          series({ points: [], intraday_available: false, provider: null, state: 'unavailable' }),
        ),
      )
      await Promise.resolve()
    })
    expect(screen.getByText('Price history unavailable')).toBeTruthy()
    expect(first.container.querySelector('.asd-chart-lw')).toBeNull()

    await act(async () => {
      firstRequest.resolve(
        okJson(series({ provider: 'yahoo', state: 'live', as_of: '2026-08-12T12:00:00Z' })),
      )
      await Promise.resolve()
    })
    expect(screen.getByText('Price history unavailable')).toBeTruthy()
    expect(first.container.querySelector('.asd-chart-lw')).toBeNull()

    first.unmount()
    const second = render(
      <AssetPriceChart instrumentId="overlap-unavailable" currency="USD" assetClass="crypto" />,
    )
    expect(screen.getByText('Loading price history…')).toBeTruthy()
    expect(screen.queryByText(/Yahoo Finance/)).toBeNull()
    expect(second.container.querySelector('.asd-chart-lw')).toBeNull()
  })

  it('applies an older successful response after a newer transport failure', async () => {
    vi.useFakeTimers()
    const firstRequest = deferred<Response>()
    mockAuthFetch
      .mockImplementationOnce(() => firstRequest.promise)
      .mockRejectedValueOnce(new Error('offline'))

    const { container } = render(
      <AssetPriceChart instrumentId="overlap-transport" currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000)
    })
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)

    await act(async () => {
      firstRequest.resolve(
        okJson(series({ provider: 'yahoo', state: 'live', as_of: '2026-08-12T12:00:00Z' })),
      )
      await Promise.resolve()
    })
    expect(screen.getByRole('status').textContent).toContain('fresh Yahoo market data')
    expect(container.querySelector('.asd-chart-lw')).toBeTruthy()
  })

  it.each(['live', 'stale'] as const)('does not cache a one-point %s series', async (state) => {
    mockAuthFetch
      .mockResolvedValueOnce(
        okJson(series({ state, provider: 'yahoo', points: [series().points[0]!] })),
      )
      .mockImplementation(() => new Promise<Response>(() => {}))
    const instrumentId = `one-point-${state}`

    const first = render(
      <AssetPriceChart instrumentId={instrumentId} currency="USD" assetClass="crypto" />,
    )
    await act(async () => {})
    first.unmount()

    render(<AssetPriceChart instrumentId={instrumentId} currency="USD" assetClass="crypto" />)
    expect(screen.getByText('Loading price history…')).toBeTruthy()
  })

  it('draws the previous-session fallback instead of the hard error, fully silently', async () => {
    // Reproduces KSTL.DE on XETRA before the regular session opens: Yahoo's chart API
    // returns zero bars for BOTH the 1m and 5m 1D probes (no pre-market print to fall back
    // on either, unlike the seeded-open-line case), so the backend widens the request and
    // serves the last complete session's real bars instead, flagged distinctly via
    // `degraded_reason: 'previous_session'` + `session_date` rather than presented as live.
    // Neither the visual "Previous session" banner NOR its aria-live disclosure render
    // anymore (owner ask 2026-09-21: "please do not show it at all" / "remove it" for the
    // aria-live announcement too) — the chart just draws the fallback tape with no comment.
    mockAuthFetch.mockResolvedValue(
      okJson(
        series({
          provider: 'yahoo',
          state: 'stale',
          degraded_reason: 'previous_session',
          session_date: '2026-09-04', // a Friday
          as_of: '2026-09-04T15:30:00Z',
        }),
      ),
    )

    const { container } = render(
      <AssetPriceChart instrumentId="kstl-closed" currency="EUR" assetClass="equity" />,
    )
    await act(async () => {})

    expect(screen.queryByText('Price history unavailable')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
    expect(container.querySelector('.asd-chart-lw')).toBeTruthy()
    expect(screen.queryByText('Previous session')).toBeNull()
    expect(screen.queryByText(/Showing Fri's session/)).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('')
    // The generic stale-series banner ("Saved data" / "Live providers are temporarily
    // unavailable.") is a DIFFERENT message and must not also render.
    expect(screen.queryByText('Saved data')).toBeNull()
    expect(screen.queryByText('Live providers are temporarily unavailable.')).toBeNull()
  })

  it('stays fully silent when session_date is absent too', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson(
        series({
          provider: 'yahoo',
          state: 'stale',
          degraded_reason: 'previous_session',
          session_date: null,
          as_of: '2026-09-04T15:30:00Z',
        }),
      ),
    )

    render(<AssetPriceChart instrumentId="kstl-closed-nodate" currency="EUR" assetClass="equity" />)
    await act(async () => {})

    expect(screen.queryByText('Previous session')).toBeNull()
    expect(screen.getByRole('status').textContent).toBe('')
  })

  it('still shows the hard unavailable error when there is truly no data anywhere', async () => {
    // No prior session either — the backend's previous-session fallback also came up
    // empty, so the original "no synthetic point" behavior must survive unchanged.
    mockAuthFetch.mockResolvedValue(
      okJson(
        series({
          points: [],
          intraday_available: false,
          provider: null,
          state: 'unavailable',
          degraded_reason: 'upstream_unavailable',
        }),
      ),
    )

    render(<AssetPriceChart instrumentId="delisted" currency="USD" assetClass="equity" />)
    await act(async () => {})

    expect(screen.getByText('Price history unavailable')).toBeTruthy()
    expect(screen.queryByText('Previous session')).toBeNull()
  })

  it('does not attribute providerless local equity history to Yahoo Finance', async () => {
    mockAuthFetch.mockResolvedValue(
      okJson(series({ price_source_id: 'MSFT', provider: null, state: 'live' })),
    )

    const { container } = render(
      <AssetPriceChart instrumentId="msft-local" currency="USD" assetClass="equity" />,
    )
    await act(async () => {})

    expect(container.querySelector('.asd-market-status')).toBeNull()
    expect(screen.queryByText(/Yahoo Finance/)).toBeNull()
    expect(screen.getByRole('status').textContent).not.toContain('Yahoo')
  })
})
