// @vitest-environment jsdom
//
// An `investment` account is worth its POSITIONS PLUS its settlement cash
// (ADR 2026-09-07-seven-account-types-one-axis) — margin is negative settlement cash, so it
// reduces the account's value. The sidebar group total REPLACED the balance with the holdings
// instead of adding them, so the owner's brokerage read S$242,279 when it is worth S$231,010:
// the S$11,269 margin debit simply disappeared from the Accounts tab.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

const HOLDINGS = 242279
const SETTLEMENT_CASH = -11269

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const brokerage = {
  id: 'acc-inv',
  name: 'IBKR',
  type: 'investment',
  currency: 'SGD',
  balance: SETTLEMENT_CASH,
  balance_base: SETTLEMENT_CASH,
  cleared_balance: SETTLEMENT_CASH,
  cleared_balance_base: SETTLEMENT_CASH,
}

function route(url: string): Promise<Response> {
  if (url === '/api/accounts') return Promise.resolve(okJson([brokerage]))
  if (url === '/api/accounts/acc-inv') return Promise.resolve(okJson(brokerage))
  if (url.startsWith('/api/portfolio'))
    return Promise.resolve(okJson({ holdings_by_account: { 'acc-inv': HOLDINGS } }))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url.startsWith('/api/transactions'))
    return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
  throw new Error(`unexpected fetch: ${url}`)
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation(route)
})

afterEach(() => vi.restoreAllMocks())

describe('useAccountsLedger — an investment account is positions PLUS settlement cash', () => {
  it('adds the margin debit to the sidebar group total instead of discarding it', async () => {
    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    await waitFor(() => expect(result.current.accounts).toHaveLength(1))

    const group = result.current.groups.find((g) => g.key === 'investment')
    expect(group?.totalBase).toBe(231010)
  })

  it('uses the same definition for the working and cleared balance card', async () => {
    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    await waitFor(() => expect(result.current.accounts).toHaveLength(1))

    expect(result.current.cashFlow.period).toBe(231010)
    expect(result.current.cashFlow.cleared).toBe(231010)
  })
})
