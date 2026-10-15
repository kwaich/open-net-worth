// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'

const mockAuthFetch = vi.fn()
vi.mock('../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { useMastheadSparkline } from './useMastheadSparkline'

function okResponse(body: object): Promise<Response> {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response)
}

function failResponse(): Promise<Response> {
  return Promise.resolve({ ok: false } as Response)
}

const SERIES_30D = {
  series: Array.from({ length: 30 }, (_, i) => ({
    date: `2026-05-${String(i + 1).padStart(2, '0')}`,
    value_base: 10000 + i * 50,
  })),
  inception: '2026-01-01',
  has_real_daily_history: true,
}

describe('useMastheadSparkline', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns values array when series has ≥ 2 points', async () => {
    mockAuthFetch.mockReturnValue(okResponse(SERIES_30D))

    const { result } = renderHook(() => useMastheadSparkline(30))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.values.length).toBe(30)
    expect(result.current.values[0]).toBeCloseTo(10000)
  })

  it('returns empty values when series has < 2 points', async () => {
    mockAuthFetch.mockReturnValue(
      okResponse({
        series: [{ date: '2026-06-01', value_base: 10000 }],
        inception: '2026-06-01',
        has_real_daily_history: false,
      }),
    )

    const { result } = renderHook(() => useMastheadSparkline(30))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.values).toHaveLength(0)
  })

  it('returns empty values when has_real_daily_history is false', async () => {
    mockAuthFetch.mockReturnValue(
      okResponse({
        ...SERIES_30D,
        has_real_daily_history: false,
      }),
    )

    const { result } = renderHook(() => useMastheadSparkline(30))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    // Flag false → no sparkline even if series has points.
    expect(result.current.values).toHaveLength(0)
  })

  it('returns empty values when API returns empty series', async () => {
    mockAuthFetch.mockReturnValue(
      okResponse({ series: [], inception: null, has_real_daily_history: false }),
    )

    const { result } = renderHook(() => useMastheadSparkline(30))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.values).toHaveLength(0)
  })

  it('returns empty values on fetch failure — never throws', async () => {
    mockAuthFetch.mockRejectedValue(new Error('network'))

    const { result } = renderHook(() => useMastheadSparkline(30))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.values).toHaveLength(0)
  })

  it('returns empty values when API returns non-ok response', async () => {
    mockAuthFetch.mockReturnValue(failResponse())

    const { result } = renderHook(() => useMastheadSparkline(30))

    await waitFor(() => {
      expect(result.current.loading).toBe(false)
    })

    expect(result.current.values).toHaveLength(0)
  })
})
