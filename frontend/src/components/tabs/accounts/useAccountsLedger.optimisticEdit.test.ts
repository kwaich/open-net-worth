// @vitest-environment jsdom
//
// Optimistic inline edits (perceived-latency fix): updateTxn used to await PUT /api/transactions/{id}
// and only THEN patch the local row, so editing a cell (memo/description/category/date) and pressing
// Enter lagged behind the server round-trip. updateTxn now:
//   1. applies the patch to the local row SYNCHRONOUSLY (before the PUT), and
//   2. RECONCILES the row to the server's authoritative fromApiTxn(raw) when the PUT resolves, and
//   3. ROLLS BACK to the pre-edit row when the PUT rejects,
// while cooperating with loadTxns's request-sequencing guard so a stale in-flight list fetch can't
// land on top of the optimistic row.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiTxn, Txn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function makeTxn(over: Partial<ApiTxn> = {}): ApiTxn {
  return {
    id: 'txn-1',
    date: '2026-06-15',
    kind: 'expense',
    category: 'food',
    description: 'Old desc',
    memo: 'old memo',
    base_amount: 10,
    base_currency: 'SGD',
    cleared: false,
    from_account_id: 'acc-1',
    from_amount: 10,
    from_currency: 'SGD',
    ...over,
  }
}

// loadCore's fixed fan-out (accounts/categories/payees/portfolio/prices) — all benign.
function mockCoreEndpoints(url: string): Promise<Response> | null {
  if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  return null
}

function isPut(opts?: RequestInit): boolean {
  return (opts as RequestInit | undefined)?.method === 'PUT'
}

beforeEach(() => {
  // The query cache is module-level: without this, a URL fetched by an earlier
  // test is served from cache here and never reaches the authFetch mock.
  clearQueryCache()
  mockAuthFetch.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAccountsLedger.updateTxn — optimistic inline edits', () => {
  it('applies the edit to the row synchronously, then reconciles to the server row on resolve', async () => {
    let resolvePut!: (r: Response) => void
    const putPromise = new Promise<Response>((r) => (resolvePut = r))

    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url.startsWith('/api/transactions/txn-1') && isPut(opts)) return putPromise
      if (url.startsWith('/api/transactions')) {
        return Promise.resolve(okJson({ transactions: [makeTxn()], total: 1, has_more: false }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.transactions).toHaveLength(1))
    expect(result.current.transactions[0]!.memo).toBe('old memo')

    // Fire the edit but DO NOT resolve the PUT — the row must already show the new value.
    let updatePromise!: Promise<Txn>
    act(() => {
      updatePromise = result.current.updateTxn('txn-1', { memo: 'new memo' })
    })
    expect(result.current.transactions[0]!.memo).toBe('new memo')

    // Resolve the PUT with the server's authoritative row (server normalized the memo) → reconcile.
    await act(async () => {
      resolvePut(okJson(makeTxn({ memo: 'NEW MEMO (server)' })))
      await updatePromise
    })
    expect(result.current.transactions[0]!.memo).toBe('NEW MEMO (server)')
  })

  it('rolls the row back to its pre-edit value when the PUT rejects, and rejects to the caller', async () => {
    let rejectPut!: (e: Error) => void
    const putPromise = new Promise<Response>((_r, rej) => (rejectPut = rej))

    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url.startsWith('/api/transactions/txn-1') && isPut(opts)) return putPromise
      if (url.startsWith('/api/transactions')) {
        return Promise.resolve(okJson({ transactions: [makeTxn()], total: 1, has_more: false }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.transactions).toHaveLength(1))

    let updatePromise!: Promise<Txn>
    act(() => {
      updatePromise = result.current.updateTxn('txn-1', { memo: 'new memo' })
    })
    // Optimistically applied first.
    expect(result.current.transactions[0]!.memo).toBe('new memo')
    // attach a handler early — the caller surfaces it as a toast. This promise only ever
    // rejects in this test (the PUT is rejected below), so the catch handler's Error is
    // the only value `caught` resolves to — cast reflects that test-specific guarantee.
    const caught = updatePromise.catch((e: Error) => e) as Promise<Error>

    await act(async () => {
      rejectPut(new Error('save failed'))
      await caught
    })

    // Rolled back to the original value; the promise rejected so AccountsTab's catch/toast fires.
    expect(result.current.transactions[0]!.memo).toBe('old memo')
    expect((await caught).message).toBe('save failed')
  })

  it('a stale in-flight list fetch resolving AFTER the edit does not overwrite the optimistic row', async () => {
    let resolveStaleList!: (r: Response) => void
    let listCalls = 0

    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      // The PUT never resolves here — we're isolating the stale LIST-fetch race.
      if (url.startsWith('/api/transactions/txn-1') && isPut(opts))
        return new Promise<Response>(() => {})
      if (url.startsWith('/api/transactions')) {
        listCalls += 1
        if (listCalls === 1) {
          // Initial load — resolves immediately with the pre-edit row.
          return Promise.resolve(okJson({ transactions: [makeTxn()], total: 1, has_more: false }))
        }
        // The second (filter-triggered) list fetch — held open so we control when it lands.
        return new Promise<Response>((r) => (resolveStaleList = r))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.transactions).toHaveLength(1))

    // Kick off a second list fetch (still carrying the pre-edit row) and leave it in flight.
    await act(async () => {
      result.current.setFilters({ dateFrom: '2026-05-01', dateTo: '2026-05-31' })
    })
    await waitFor(() => expect(listCalls).toBe(2))

    // Edit while that fetch is in flight — optimistic apply + abort of the in-flight fetch.
    act(() => {
      void result.current.updateTxn('txn-1', { memo: 'new memo' })
    })
    expect(result.current.transactions[0]!.memo).toBe('new memo')

    // The stale fetch now lands with the pre-edit row — it must be dropped, not applied.
    await act(async () => {
      resolveStaleList(
        okJson({ transactions: [makeTxn({ memo: 'old memo' })], total: 1, has_more: false }),
      )
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(result.current.transactions[0]!.memo).toBe('new memo')
  })
})
