// @vitest-environment jsdom
//
// detachSourceRef (CDS-6, ADR 2026-09-07) — the "Unlink source" write choke on the frontend
// side. POSTs /api/transactions/{id}/refs/detach and patches the row's externalRefs from the
// response onto the caller's own copy (the route replies with only {ok, transaction_id,
// external_refs}, never a full transaction). Mirrors the sibling toggleCleared/updateTxn tests
// in this file: non-ok throws, the local list is patched, and fireMutate() runs on success —
// the onMutate callback wired as `refresh` in AccountsTab.tsx.

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

function makeTxn(over: Partial<ApiTxn> = {}): ApiTxn {
  return {
    id: 'txn-1',
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
    external_refs: [{ source: 'plaid', ref: 't1' }],
    ...over,
  }
}

function mockCoreEndpoints(url: string): Promise<Response> | null {
  if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url === '/api/transactions' || url.startsWith('/api/transactions?'))
    return Promise.resolve(okJson({ transactions: [makeTxn()], total: 1, has_more: false }))
  return null
}

beforeEach(() => {
  mockAuthFetch.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAccountsLedger.detachSourceRef', () => {
  it('throws on a non-ok response and leaves the row untouched', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url === '/api/transactions/txn-1/refs/detach') {
        return Promise.resolve({ ok: false, text: async () => 'pair not held' } as Response)
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.transactions).toHaveLength(1))
    const txn = result.current.transactions[0]!

    await expect(
      act(async () => {
        await result.current.detachSourceRef(txn, 'plaid', 't1')
      }),
    ).rejects.toThrow()

    // The refusal never touched the local row — still carrying its original ref.
    expect(result.current.transactions[0]!.externalRefs).toEqual([{ source: 'plaid', ref: 't1' }])
  })

  it('coerces a null/missing source in the response and patches the transaction list', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url === '/api/transactions/txn-1/refs/detach') {
        return Promise.resolve(
          okJson({
            ok: true,
            transaction_id: 'txn-1',
            // Backend's remaining pair carries no `source` at all — the frontend type says
            // `source: string | null`, so this must land as an explicit null, not `undefined`.
            external_refs: [{ ref: 'statement:abc' }],
          }),
        )
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.transactions).toHaveLength(1))
    const txn = result.current.transactions[0]!

    let updated!: Awaited<ReturnType<typeof result.current.detachSourceRef>>
    await act(async () => {
      updated = await result.current.detachSourceRef(txn, 'plaid', 't1')
    })

    expect(updated.externalRefs).toEqual([{ source: null, ref: 'statement:abc' }])
    // The transaction LIST is patched too, not just the returned value.
    expect(result.current.transactions[0]!.externalRefs).toEqual([
      { source: null, ref: 'statement:abc' },
    ])
  })

  it('calls the onMutate callback after a successful detach', async () => {
    mockAuthFetch.mockImplementation((url: string) => {
      const core = mockCoreEndpoints(url)
      if (core) return core
      if (url === '/api/transactions/txn-1/refs/detach') {
        return Promise.resolve(okJson({ ok: true, transaction_id: 'txn-1', external_refs: [] }))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const onMutate = vi.fn()
    const { result } = renderHook(() => useAccountsLedger(onMutate))
    await waitFor(() => expect(result.current.transactions).toHaveLength(1))
    const txn = result.current.transactions[0]!
    expect(onMutate).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.detachSourceRef(txn, 'plaid', 't1')
    })

    expect(onMutate).toHaveBeenCalledTimes(1)
    expect(result.current.transactions[0]!.externalRefs).toEqual([])
  })
})
