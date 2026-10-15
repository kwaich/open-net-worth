// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { clearQueryCache } from '../state/queryCache'

// Mock authFetch so we control API responses without a running server.
const mockAuthFetch = vi.fn()
vi.mock('../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { useInvestmentSignals } from './useInvestmentSignals'

// ── Helpers ───────────────────────────────────────────────────────────────────

function okResponse(body: object): Promise<Response> {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(body),
  } as Response)
}

function failResponse(): Promise<Response> {
  return Promise.resolve({ ok: false } as Response)
}

const CANNED_BRIEF = {
  signals: [
    {
      id: 'concentration',
      kind: 'concentration',
      tone: 'warn',
      actionable: true,
      tag: 'Concentration',
      narrative:
        'AAPL is 50% of investments, 30 percentage points above the 20% guideline. Consider trimming.',
      amount: 5000,
    },
    {
      id: 'income',
      kind: 'income',
      tone: 'good',
      actionable: false,
      tag: 'Income',
      narrative: 'MSFT goes ex-div in 5 days — SGD 100.00 projected.',
      amount: 100,
    },
  ],
  decisions_count: 1,
  base_currency: 'SGD',
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('useInvestmentSignals (thin fetch)', () => {
  beforeEach(() => {
    clearQueryCache()
    vi.clearAllMocks()
    mockAuthFetch.mockResolvedValue({ ok: false })
  })

  it('returns empty signals on initial load (loading state)', () => {
    // Never resolves — stays in loading state.
    mockAuthFetch.mockReturnValue(new Promise(() => {}))
    const { result } = renderHook(() => useInvestmentSignals())
    // Before fetch resolves, signals should be empty.
    expect(result.current.signals).toEqual([])
    expect(result.current.decisionsCount).toBe(0)
  })

  it('maps backend signals and decisions_count correctly on successful fetch', async () => {
    mockAuthFetch.mockResolvedValue(okResponse(CANNED_BRIEF))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals).toHaveLength(2)
    expect(result.current.signals[0]!.kind).toBe('concentration')
    expect(result.current.signals[0]!.actionable).toBe(true)
    expect(result.current.signals[1]!.kind).toBe('income')
    expect(result.current.signals[1]!.actionable).toBe(false)
    // decisions_count from backend (snake_case → camelCase)
    expect(result.current.decisionsCount).toBe(1)
  })

  it('falls back to ONTRACK when fetch fails with non-ok response', async () => {
    mockAuthFetch.mockResolvedValue(failResponse())

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals[0]!.kind).toBe('ontrack')
    expect(result.current.signals[0]!.actionable).toBe(false)
    expect(result.current.decisionsCount).toBe(0)
  })

  it('falls back to ONTRACK when fetch throws a network error', async () => {
    mockAuthFetch.mockRejectedValue(new Error('network'))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals[0]!.kind).toBe('ontrack')
    expect(result.current.decisionsCount).toBe(0)
  })

  it('falls back to ONTRACK when backend returns malformed response', async () => {
    mockAuthFetch.mockResolvedValue(okResponse({ unexpected_shape: true }))

    const { result } = renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(result.current.signals.length).toBeGreaterThan(0)
    })

    expect(result.current.signals[0]!.kind).toBe('ontrack')
    expect(result.current.decisionsCount).toBe(0)
  })

  it('fetches /api/investments/brief (correct endpoint)', async () => {
    mockAuthFetch.mockResolvedValue(okResponse(CANNED_BRIEF))

    renderHook(() => useInvestmentSignals())

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/investments/brief')
    })
  })

  it('keeps the previous brief visible while a remount revalidates', async () => {
    mockAuthFetch.mockResolvedValueOnce(okResponse(CANNED_BRIEF))
    const first = renderHook(() => useInvestmentSignals())
    await waitFor(() => expect(first.result.current.signals).toHaveLength(2))
    first.unmount()

    let resolveRefresh!: (value: Response) => void
    mockAuthFetch.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          resolveRefresh = resolve
        }),
    )
    const second = renderHook(() => useInvestmentSignals())

    expect(second.result.current.signals).toHaveLength(2)
    expect(second.result.current.loading).toBe(false)

    await act(async () => resolveRefresh(await okResponse(CANNED_BRIEF)))
  })
})
