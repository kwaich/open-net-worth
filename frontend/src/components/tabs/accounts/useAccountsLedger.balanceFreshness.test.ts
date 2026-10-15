// @vitest-environment jsdom
//
// Regression: the account balance card (Cleared + Uncleared = Working) renders from the `accounts`
// state, but the ledger mutations only re-read an account when the REQUEST PAYLOAD happened to name
// one (`data.from_account_id` / `data.to_account_id`). So:
//   - toggleCleared never refreshed any account  → cleared/uncleared figures froze on a ✓/○ tap
//   - an amount-only updateTxn named no account  → the same staleness on an inline amount edit
//   - deleteTxn / bulkDelete never refreshed     → ditto
// fireMutate() does NOT cover this: it invalidates the query cache and pokes the app-wide refresh,
// neither of which writes this hook's `accounts` state.
//
// Every ledger write must re-read account balances.

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

function makeTxn(id: string, cleared: boolean, amount = 10): ApiTxn {
  return {
    id,
    date: '2026-06-15',
    kind: 'expense',
    category: null,
    description: '',
    base_amount: amount,
    base_currency: 'SGD',
    cleared,
    from_account_id: 'acc-1',
    from_amount: amount,
    from_currency: 'SGD',
  }
}

/** Server-side account state the mock serves — mutated mid-test to model a write landing. */
let serverCleared = 0

function makeAccount() {
  return {
    id: 'acc-1',
    name: 'QuickPay Wallet',
    type: 'cash',
    currency: 'SGD',
    balance: 100,
    balance_base: 100,
    cleared_balance: serverCleared,
    cleared_balance_base: serverCleared,
  }
}

/**
 * Routes the mock. Specific endpoints are matched BEFORE the prefix fallbacks, so a
 * PATCH /api/transactions/<id>/cleared is not swallowed by the /api/transactions list branch.
 */
function route(url: string, init?: RequestInit): Promise<Response> {
  const method = init?.method ?? 'GET'
  if (url === '/api/transactions/txn-1/cleared')
    return Promise.resolve(okJson(makeTxn('txn-1', true)))
  if (url === '/api/transactions/txn-1' && method === 'PUT')
    return Promise.resolve(okJson(makeTxn('txn-1', false, 42)))
  if (url === '/api/transactions/txn-1' && method === 'DELETE') return Promise.resolve(okJson({}))
  if (url === '/api/accounts' || url === '/api/accounts/acc-1') {
    return Promise.resolve(okJson(url === '/api/accounts' ? [makeAccount()] : makeAccount()))
  }
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url.startsWith('/api/transactions'))
    return Promise.resolve(
      okJson({ transactions: [makeTxn('txn-1', false)], total: 1, has_more: false }),
    )
  throw new Error(`unexpected fetch: ${method} ${url}`)
}

beforeEach(() => {
  serverCleared = 0
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation(route)
})

afterEach(() => {
  vi.restoreAllMocks()
})

async function mountLedger() {
  const { result } = renderHook(() => useAccountsLedger())
  await waitFor(() => expect(result.current.hasLoaded).toBe(true))
  await waitFor(() => expect(result.current.accounts).toHaveLength(1))
  expect(result.current.accounts[0]?.clearedBalance).toBe(0)
  return result
}

describe('useAccountsLedger — account balances stay fresh after a ledger write', () => {
  it('re-reads the cleared balance after toggleCleared', async () => {
    const result = await mountLedger()
    serverCleared = 60

    await act(async () => {
      await result.current.toggleCleared('txn-1', true)
    })

    await waitFor(() => expect(result.current.accounts[0]?.clearedBalance).toBe(60))
    expect(result.current.cashFlow.cleared).toBe(60)
  })

  it('re-reads balances after an amount-only updateTxn (payload names no account)', async () => {
    const result = await mountLedger()
    serverCleared = 42

    await act(async () => {
      await result.current.updateTxn('txn-1', { from_amount: 42 })
    })

    await waitFor(() => expect(result.current.accounts[0]?.clearedBalance).toBe(42))
  })

  it('re-reads balances after deleteTxn', async () => {
    const result = await mountLedger()
    serverCleared = 7

    await act(async () => {
      await result.current.deleteTxn('txn-1')
    })

    await waitFor(() => expect(result.current.accounts[0]?.clearedBalance).toBe(7))
  })
})
