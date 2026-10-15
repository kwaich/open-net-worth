// @vitest-environment jsdom
//
// Tests for useAnalystConsensus: the session cache (miss → fetch → cache, hit →
// instant no-fetch), null-coverage caching, and error not poisoning the cache.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, waitFor, cleanup } from '@testing-library/react'
import { useAnalystConsensus, _clearAnalystCache } from './useAnalystConsensus'
import type { AnalystResponse } from './useAnalystConsensus'

vi.mock('../api/client', () => ({
  authFetch: vi.fn(),
}))

import { authFetch } from '../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

const COVERAGE: AnalystResponse = {
  currency: 'USD',
  analyst: { rating_label: 'Buy', rating_key: 'buy', analyst_count: 12, target_mean: 42 },
  ratings: [{ date: '2026-06-01', firm: 'Acme', rating: 'Buy', target: 50 }],
}

beforeEach(() => {
  _clearAnalystCache()
  mockAuthFetch.mockReset()
})

afterEach(() => {
  cleanup()
})

describe('useAnalystConsensus', () => {
  it('cache miss: starts loading, fetches once, then surfaces data', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson(COVERAGE))

    const { result } = renderHook(() => useAnalystConsensus('inst-1'))

    expect(result.current.loading).toBe(true)
    expect(result.current.data).toBeNull()

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual(COVERAGE)
    expect(mockAuthFetch).toHaveBeenCalledTimes(1)
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/intel/assets/inst-1/analyst')
  })

  it('cache hit: a second mount is instant — no loading flash, no re-fetch', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson(COVERAGE))
    const first = renderHook(() => useAnalystConsensus('inst-1'))
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    first.unmount()

    // Remount the same instrument — should read straight from the cache.
    const { result } = renderHook(() => useAnalystConsensus('inst-1'))
    expect(result.current.loading).toBe(false)
    expect(result.current.data).toEqual(COVERAGE)
    expect(mockAuthFetch).toHaveBeenCalledTimes(1) // still just the first fetch
  })

  it('caches a null (no-coverage) result so crypto/commodities never re-fetch', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson(null))
    const first = renderHook(() => useAnalystConsensus('crypto-1'))
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    expect(first.result.current.data).toBeNull()
    first.unmount()

    const { result } = renderHook(() => useAnalystConsensus('crypto-1'))
    expect(result.current.loading).toBe(false)
    expect(result.current.data).toBeNull()
    expect(mockAuthFetch).toHaveBeenCalledTimes(1)
  })

  it('does not cache a network error — a later mount retries', async () => {
    mockAuthFetch.mockRejectedValueOnce(new Error('offline'))
    const first = renderHook(() => useAnalystConsensus('inst-2'))
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    expect(first.result.current.data).toBeNull()
    first.unmount()

    mockAuthFetch.mockResolvedValueOnce(okJson(COVERAGE))
    const { result } = renderHook(() => useAnalystConsensus('inst-2'))
    expect(result.current.loading).toBe(true) // not cached → retries
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual(COVERAGE)
    expect(mockAuthFetch).toHaveBeenCalledTimes(2)
  })

  it('refetches when the instrumentId changes', async () => {
    mockAuthFetch.mockResolvedValueOnce(okJson(COVERAGE))
    const { result, rerender } = renderHook(({ id }) => useAnalystConsensus(id), {
      initialProps: { id: 'inst-1' },
    })
    await waitFor(() => expect(result.current.loading).toBe(false))

    mockAuthFetch.mockResolvedValueOnce(okJson({ ...COVERAGE, currency: 'EUR' }))
    rerender({ id: 'inst-9' })
    await waitFor(() => expect(result.current.data?.currency).toBe('EUR'))
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/intel/assets/inst-9/analyst')
  })
})
