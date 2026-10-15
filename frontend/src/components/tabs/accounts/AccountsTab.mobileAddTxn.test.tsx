// @vitest-environment jsdom
//
// On mobile, AddRow's dense multi-column grid form has no responsive layout (unlike every
// other register row, which already swaps to stacked cards below 640px). Rather than showing
// that desktop-sized inline form on a phone, tapping "Add transaction" on mobile hands off to
// Lucius instead: it stages a prefilled composer message and opens the Lucius tab. Desktop is
// unchanged — it still opens the inline AddRow form.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import { ChatComposerProvider, useChatComposerOptional } from '../../../state/chatComposer'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount, ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
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

// A seeded row so the register isn't in its zero-state — that state renders a SECOND
// "Add transaction" button (Register.tsx's `.acct-empty-btn`), which would make
// `getByRole('button', { name: 'Add transaction' })` ambiguous.
const TXN: ApiTxn = {
  id: 't1',
  date: '2026-06-01',
  kind: 'expense',
  category: null,
  description: 'Coffee',
  base_amount: 5,
  base_currency: 'SGD',
  cleared: true,
  needs_review: false,
  from_account_id: 'acc-1',
  from_amount: 5,
  from_currency: 'SGD',
} as ApiTxn

function setupMocks() {
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions'))
      return Promise.resolve(okJson({ transactions: [TXN], total: 1, has_more: false }))
    return Promise.resolve(okJson({}))
  })
}

function mockMobile(isMobile: boolean) {
  Object.defineProperty(window, 'innerWidth', {
    writable: true,
    configurable: true,
    value: isMobile ? 390 : 1400,
  })
  vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
    matches: isMobile && query === '(max-width: 639.98px)',
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }))
}

// Renders AccountsTab inside a real ChatComposerProvider and a spy component that surfaces
// pendingText, so the test can assert on the staged prefill without reaching into RouterShell.
let capturedPendingText: string | null = null
function PendingTextSpy() {
  const chatComposer = useChatComposerOptional()
  capturedPendingText = chatComposer?.pendingText ?? null
  return null
}

function renderTab(selectedAccountId: string | null) {
  return render(
    <ChatComposerProvider>
      <PendingTextSpy />
      <AccountsTab selectedAccountId={selectedAccountId} />
    </ChatComposerProvider>,
  )
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  capturedPendingText = null
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('AccountsTab — mobile "Add transaction" hands off to Lucius', () => {
  it('on mobile with an account selected, stages "Add a transaction to {account}: " and opens Lucius instead of the inline form', async () => {
    mockMobile(true)
    setupMocks()
    renderTab(ACCOUNT.id)
    // Two gates, and both are load-bearing. The header must be THIS account's: until the
    // accounts fetch resolves `selectedAccountId` matches nothing and the tab renders the
    // Overview, whose title element is also `.acct-head-title` (reading "Accounts"). And the
    // seeded row must have landed: until it does the register is in its zero-state, which
    // renders a SECOND "Add transaction" button and makes `getByRole` ambiguous (see the
    // note on TXN above). Waiting only on the title left both races open. The second gate
    // asserts that precondition directly rather than proxying it through a row's text, which
    // is not a unique match on every breakpoint.
    await waitFor(() =>
      expect(document.querySelector('.acct-head-title')?.textContent).toBe(ACCOUNT.name),
    )
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Add transaction' })).toHaveLength(1),
    )

    const dockOpen = vi.fn()
    window.addEventListener('lx-dock-open', dockOpen)

    fireEvent.click(screen.getByRole('button', { name: 'Add transaction' }))

    expect(capturedPendingText).toBe('Add a transaction to Meridian Savings: ')
    expect(dockOpen).toHaveBeenCalledTimes(1)
    expect(document.querySelector('.acct-addrow-grid')).toBeNull()

    window.removeEventListener('lx-dock-open', dockOpen)
  })

  it('on mobile with no account selected, stages "Add a transaction: "', async () => {
    mockMobile(true)
    setupMocks()
    renderTab(null)
    await screen.findByText('Coffee')

    fireEvent.click(screen.getByRole('button', { name: 'Add transaction' }))

    expect(capturedPendingText).toBe('Add a transaction: ')
  })

  it('on desktop, still opens the inline AddRow form and never touches the chat composer', async () => {
    mockMobile(false)
    setupMocks()
    renderTab(ACCOUNT.id)
    // Two gates, and both are load-bearing. The header must be THIS account's: until the
    // accounts fetch resolves `selectedAccountId` matches nothing and the tab renders the
    // Overview, whose title element is also `.acct-head-title` (reading "Accounts"). And the
    // seeded row must have landed: until it does the register is in its zero-state, which
    // renders a SECOND "Add transaction" button and makes `getByRole` ambiguous (see the
    // note on TXN above). Waiting only on the title left both races open. The second gate
    // asserts that precondition directly rather than proxying it through a row's text, which
    // is not a unique match on every breakpoint.
    await waitFor(() =>
      expect(document.querySelector('.acct-head-title')?.textContent).toBe(ACCOUNT.name),
    )
    await waitFor(() =>
      expect(screen.getAllByRole('button', { name: 'Add transaction' })).toHaveLength(1),
    )

    const dockOpen = vi.fn()
    window.addEventListener('lx-dock-open', dockOpen)

    fireEvent.click(screen.getByRole('button', { name: 'Add transaction' }))

    expect(document.querySelector('.acct-addrow-grid')).toBeTruthy()
    expect(dockOpen).not.toHaveBeenCalled()
    expect(capturedPendingText).toBeNull()

    window.removeEventListener('lx-dock-open', dockOpen)
  })
})
