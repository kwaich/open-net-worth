// @vitest-environment jsdom
//
// Regression: on initial mount, accounts/categories/payees/portfolio resolve and the shell
// renders while /api/transactions is still in flight. The register used to key its empty-state
// purely off `transactions.length === 0`, so that window showed "No transactions yet" (with its
// Add/Import CTAs) instead of a loading indicator — indistinguishable from a genuinely empty
// account. `loadingTxns` must gate the empty-state branch.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}

const ACCOUNT: ApiAccount = {
  id: 'acc-1',
  name: 'Meridian Savings',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  balance: 1250,
  balance_base: 1250,
  cleared_balance: 1250,
  cleared_balance_base: 1250,
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('AccountsTab — register loading state while transactions fetch is in flight', () => {
  it('shows a loading indicator, not the empty-state CTAs, while /api/transactions is pending', async () => {
    let resolveTxns!: (v: Response) => void
    const txnsPromise = new Promise<Response>((resolve) => {
      resolveTxns = resolve
    })

    mockAuthFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
      if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
      if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
      if (url.startsWith('/api/transactions')) return txnsPromise
      return Promise.resolve(okJson({}))
    })

    render(<AccountsTab selectedAccountId={ACCOUNT.id} />)

    // The account shell (core data) has resolved and rendered...
    await waitFor(() =>
      expect(document.querySelector('.acct-head-title')?.textContent).toBe(ACCOUNT.name),
    )
    // ...but the txn fetch is still pending, so the register must show a loading state, never
    // the "No transactions yet" empty-state CTAs.
    expect(screen.getByText(/Loading transactions/i)).toBeTruthy()
    expect(screen.queryByText(/No transactions yet/i)).toBeNull()

    resolveTxns(okJson({ transactions: [], total: 0, has_more: false }))

    await waitFor(() => expect(screen.getByText(/No transactions yet/i)).toBeTruthy())
    expect(screen.queryByText(/Loading transactions/i)).toBeNull()
  })
})
