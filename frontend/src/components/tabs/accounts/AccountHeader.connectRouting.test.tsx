// @vitest-environment jsdom
//
// CDS-UI-21 (docs/qa/acceptance/connectors-data-sources.md) — mirrors CDS-UI-19
// (AccountsTab.emptyRoutes.test.tsx): the account header's kebab "Connect to bank" action used
// to open ConnectModal, a fake review-then-spinner flow that wrote `connected: true` — a write
// the backend now silently drops (RFC connectors-as-data-sources §D: connected is derived,
// never caller-writable), leaving a misleading "X connected" toast with nothing actually
// linked. Routed the same way Region 5 fixed the Add Account dead end: straight to the
// Connectors tab, no toast, no fake modal. ConnectModal.tsx is deleted — this was its only
// caller.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import type { ApiAccount } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const MANUAL_ACCOUNT: ApiAccount = {
  id: 'acc-manual',
  name: 'Cash Savings',
  kind: 'asset',
  type: 'savings',
  currency: 'SGD',
  connected: false,
  balance: 100,
  balance_base: 100,
  cleared_balance: 100,
  cleared_balance_base: 100,
}

function setupMocks() {
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([MANUAL_ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions')) {
      return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  setupMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('AccountHeader "Connect to bank" routes to Connectors (CDS-UI-21)', () => {
  it('navigates to Connectors with no toast and never writes connected', async () => {
    const onNavigate = vi.fn()
    render(<AccountsTab selectedAccountId={MANUAL_ACCOUNT.id} onNavigate={onNavigate} />)
    await waitFor(() => expect(document.querySelector('.acct-head-title')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'More actions' }))
    fireEvent.click(screen.getByRole('button', { name: /Connect to bank/ }))

    expect(onNavigate).toHaveBeenCalledWith('connectors')
    expect(screen.queryByText(/connected$/i)).toBeNull()
    expect(screen.queryByRole('dialog', { name: 'Connect to bank' })).toBeNull()
    expect(
      mockAuthFetch.mock.calls.some(
        (c) =>
          String(c[0]) === `/api/accounts/${MANUAL_ACCOUNT.id}` &&
          (c[1] as RequestInit)?.method === 'PUT',
      ),
    ).toBe(false)
  })
})
