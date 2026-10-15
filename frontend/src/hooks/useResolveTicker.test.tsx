// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'
import { useResolveTicker } from './useResolveTicker'
import { useAssetDetail, _clearDetailCache } from './useAssetDetail'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

function makeDetail(instrumentId: string, over: Record<string, unknown> = {}) {
  return {
    instrument_id: instrumentId,
    has_ticker: true,
    asset: { ticker: 'ONWAI.DE', name: 'ONWAI Inc', asset_class: 'equity' },
    price: { last: 42, currency: 'EUR', as_of: new Date().toISOString() },
    news: [],
    key_issues: [],
    last_refreshed_at: new Date().toISOString(),
    ...over,
  }
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  _clearDetailCache()
})
afterEach(() => cleanup())

describe('useResolveTicker', () => {
  it('resolves a ticker to its instrument id via GET /api/intel/assets/by-ticker/{ticker}', async () => {
    mockAuthFetch.mockResolvedValue(okJson(makeDetail('inst-onwai')))

    const { result } = renderHook(() => useResolveTicker('ONWAI.DE'))
    expect(result.current.resolving).toBe(true)
    expect(result.current.instrumentId).toBeNull()

    await waitFor(() => expect(result.current.instrumentId).toBe('inst-onwai'))
    expect(result.current.resolving).toBe(false)
    expect(result.current.error).toBe(false)
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/intel/assets/by-ticker/ONWAI.DE')
  })

  it('URL-encodes the ticker', async () => {
    mockAuthFetch.mockResolvedValue(okJson(makeDetail('inst-x')))
    renderHook(() => useResolveTicker('BRK B'))
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/intel/assets/by-ticker/BRK%20B')
  })

  it('sets error on a non-ok response (unresolvable ticker)', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, status: 404 } as Response)

    const { result } = renderHook(() => useResolveTicker('XYZJUNK'))
    await waitFor(() => expect(result.current.error).toBe(true))
    expect(result.current.instrumentId).toBeNull()
    expect(result.current.resolving).toBe(false)
  })

  it('sets error on a network failure', async () => {
    mockAuthFetch.mockRejectedValue(new Error('network down'))

    const { result } = renderHook(() => useResolveTicker('ONWAI.DE'))
    await waitFor(() => expect(result.current.error).toBe(true))
  })

  it('re-resolves when the ticker prop changes', async () => {
    mockAuthFetch.mockImplementation((url: string) =>
      Promise.resolve(okJson(makeDetail(url.includes('NVDA') ? 'inst-nvda' : 'inst-onwai'))),
    )
    const { result, rerender } = renderHook(({ ticker }) => useResolveTicker(ticker), {
      initialProps: { ticker: 'ONWAI.DE' },
    })
    await waitFor(() => expect(result.current.instrumentId).toBe('inst-onwai'))

    rerender({ ticker: 'NVDA' })
    expect(result.current.resolving).toBe(true) // resets while the new ticker resolves
    await waitFor(() => expect(result.current.instrumentId).toBe('inst-nvda'))
  })

  it('retry() re-fires the resolve after an error', async () => {
    mockAuthFetch.mockResolvedValueOnce({ ok: false, status: 500 } as Response)
    const { result } = renderHook(() => useResolveTicker('ONWAI.DE'))
    await waitFor(() => expect(result.current.error).toBe(true))

    mockAuthFetch.mockResolvedValueOnce(okJson(makeDetail('inst-onwai')))
    act(() => result.current.retry())
    await waitFor(() => expect(result.current.instrumentId).toBe('inst-onwai'))
  })

  it("seeds useAssetDetail's cache so a same-id mount paints instantly, no skeleton flash", async () => {
    mockAuthFetch.mockResolvedValue(
      okJson(
        makeDetail('inst-onwai', {
          price: { last: 77, currency: 'EUR', as_of: new Date().toISOString() },
        }),
      ),
    )

    const { result } = renderHook(() => useResolveTicker('ONWAI.DE'))
    await waitFor(() => expect(result.current.instrumentId).toBe('inst-onwai'))

    // A fresh useAssetDetail mount for the SAME id must show the seeded data
    // immediately (loading=false on first render), not the skeleton.
    const detailHook = renderHook(() => useAssetDetail('inst-onwai'))
    expect(detailHook.result.current.loading).toBe(false)
    expect(detailHook.result.current.data?.price.last).toBe(77)
  })
})
