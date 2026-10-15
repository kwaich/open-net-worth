// @vitest-environment jsdom
//
// Regression: toggleCleared() patched the local transaction row on a successful PATCH but,
// unlike bulkUpdate/createTxn/updateTxn/deleteTxn right alongside it, never called fireMutate()
// — the onMutate callback wired as `refresh` in AccountsTab.tsx ("every write also refreshes
// app-wide net worth"). So toggling a transaction's cleared checkbox left the account header's
// cleared/uncleared/working balance figures stale until some unrelated mutation triggered a
// refresh.
//
// This test asserts the onMutate callback fires after a successful toggleCleared call.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'
import type { ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

function makeTxn(id: string, cleared: boolean): ApiTxn {
  return {
    id,
    date: '2026-06-15',
    kind: 'expense',
    category: 'food',
    description: '',
    base_amount: 10,
    base_currency: 'SGD',
    cleared,
    from_account_id: 'acc-1',
    from_amount: 10,
    from_currency: 'SGD',
  }
}

function mockCoreEndpoints(url: string): Promise<Response> | null {
  if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url.startsWith('/api/transactions'))
    return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
  return null
}

beforeEach(() => {
  mockAuthFetch.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAccountsLedger.toggleCleared — refreshes app-wide data on success', () => {
  it('calls the onMutate callback after a successful cleared toggle', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url.startsWith('/api/transactions/txn-1/cleared')) {
        return Promise.resolve(okJson(makeTxn('txn-1', true)))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const onMutate = vi.fn()
    const { result } = renderHook(() => useAccountsLedger(onMutate))

    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    expect(onMutate).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.toggleCleared('txn-1', true)
    })

    expect(onMutate).toHaveBeenCalledTimes(1)
  })
})
