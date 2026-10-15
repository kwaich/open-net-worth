// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const createdCreditAccount = {
  id: 'acc-credit',
  name: 'Rewards card',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
  balance: 0,
  balance_base: 0,
  cleared_balance: 0,
  cleared_balance_base: 0,
  payment_envelope_sync_warning: {
    code: 'payment_envelope_sync_failed',
    message:
      'Account created, but its credit-card payment envelope could not be synced. The app will retry reconciliation on startup.',
    retryable: true,
  },
}

function route(url: string, init?: RequestInit): Promise<Response> {
  if (url === '/api/accounts' && init?.method === 'POST') {
    return Promise.resolve(okJson(createdCreditAccount))
  }
  if (url === '/api/accounts') return Promise.resolve(okJson([]))
  if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson({ holdings_by_account: {} }))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url.startsWith('/api/transactions')) {
    return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
  }
  throw new Error(`unexpected fetch: ${url}`)
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation(route)
})

afterEach(() => vi.restoreAllMocks())

describe('useAccountsLedger.saveAccount', () => {
  it('keeps a committed create successful while surfacing its payment-envelope warning', async () => {
    const { result } = renderHook(() => useAccountsLedger())
    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    const onNotice = vi.fn()

    await act(async () => {
      const account = await result.current.saveAccount(
        { name: 'Rewards card', type: 'credit', currency: 'SGD', initial_balance: 0 },
        undefined,
        onNotice,
      )
      expect(account.id).toBe('acc-credit')
    })

    expect(onNotice).toHaveBeenCalledWith(
      createdCreditAccount.payment_envelope_sync_warning.message,
    )
    expect(result.current.accounts).toHaveLength(1)
  })
})
