// @vitest-environment jsdom
//
// REG2 regression: loadTxns had no request-sequencing/cancellation guard. Every response
// unconditionally called setTransactions/setTotal/setHasMore regardless of whether a NEWER
// request had since superseded it — so an older in-flight fetch (e.g. the initial unfiltered
// mount fetch) resolving AFTER a newer one (e.g. a just-applied date filter) silently clobbered
// the correct, filtered state with stale data (reported: chip "In May 2026" + footer/rows still
// showing the full unfiltered ledger).
//
// This test drives that exact sequence with two manually-resolvable promises, resolving the
// OLD (first-fired) request LAST, and asserts the final state reflects the NEW request only.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function makeTxn(id: string): ApiTxn {
  return {
    id,
    date: '2026-06-15',
    kind: 'expense',
    category: 'food',
    description: '',
    base_amount: 10,
    base_currency: 'SGD',
    cleared: true,
    from_account_id: 'acc-1',
    from_amount: 10,
    from_currency: 'SGD',
  }
}

// loadCore's fixed fan-out (accounts/categories/payees/portfolio/prices) — all empty/benign so
// loadCore resolves cleanly and hasLoaded flips true without needing to assert on it.
function mockCoreEndpoints(url: string): Promise<Response> | null {
  if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  return null
}

beforeEach(() => {
  mockAuthFetch.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAccountsLedger — loadTxns request-sequencing guard (REG2)', () => {
  beforeEach(() => clearQueryCache())
  it('an older in-flight fetch resolving AFTER a newer one does not clobber the newer state', async () => {
    let resolveStale!: (r: Response) => void
    let staleCallCount = 0

    mockAuthFetch.mockImplementation((url: string) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url.startsWith('/api/transactions')) {
        staleCallCount += 1
        if (staleCallCount === 1) {
          // The initial unfiltered mount fetch — held open so we control when it resolves.
          return new Promise<Response>((r) => (resolveStale = r))
        }
        // The second (filter-driven) fetch — resolves immediately with the CORRECT, filtered data.
        return Promise.resolve(
          okJson({ transactions: [makeTxn('may-1')], total: 3, has_more: false }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())

    // Wait for the initial (stale) loadTxns call to have fired.
    await waitFor(() => expect(staleCallCount).toBe(1))

    // Before the stale fetch resolves, the user applies a date filter — this is the newer request.
    await act(async () => {
      result.current.setFilters({ dateFrom: '2026-05-01', dateTo: '2026-05-31' })
    })

    // The newer (filtered) fetch has already landed.
    await waitFor(() => expect(result.current.total).toBe(3))
    expect(result.current.transactions.map((t) => t.id)).toEqual(['may-1'])

    // NOW resolve the stale, unfiltered request — arriving last.
    await act(async () => {
      resolveStale(
        okJson({ transactions: [makeTxn('jun-1'), makeTxn('jun-2')], total: 1184, has_more: true }),
      )
      await Promise.resolve()
      await Promise.resolve()
    })

    // The stale response must NOT have overwritten the correct, filtered state.
    expect(result.current.total).toBe(3)
    expect(result.current.transactions.map((t) => t.id)).toEqual(['may-1'])
    expect(result.current.hasMore).toBe(false)
  })

  it('a superseded request does not set an error, even if it later fails', async () => {
    let rejectStale!: (e: Error) => void
    let staleCallCount = 0

    mockAuthFetch.mockImplementation((url: string) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url.startsWith('/api/transactions')) {
        staleCallCount += 1
        if (staleCallCount === 1) {
          return new Promise<Response>((_r, rej) => (rejectStale = rej))
        }
        return Promise.resolve(okJson({ transactions: [makeTxn('t2')], total: 1, has_more: false }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(staleCallCount).toBe(1))

    await act(async () => {
      result.current.setFilters({ dateFrom: '2026-05-01', dateTo: '2026-05-31' })
    })
    await waitFor(() => expect(result.current.total).toBe(1))

    // The stale (superseded) request now fails — this must NOT surface as a UI error, since a
    // deliberate supersede is not a real failure (no spurious error toast on every filter change).
    await act(async () => {
      rejectStale(new Error('network blip'))
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(result.current.error).toBeNull()
    expect(result.current.total).toBe(1)
  })
})
