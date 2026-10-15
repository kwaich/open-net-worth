// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { AccountsTab } from './AccountsTab'
import { TransactionSurfacesProvider } from './TransactionSurfaces'
import { useTransactionSurfaces } from './transactionSurfacesContext'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount, ApiTxn } from './types'

const { sendMessage } = vi.hoisted(() => ({ sendMessage: vi.fn(async () => undefined) }))

vi.mock('../../../state/luciusChat', () => ({
  useLuciusChatOptional: () => ({ sendMessage }),
}))
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
  name: 'Everyday Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  balance: 886.71,
  balance_base: 886.71,
  cleared_balance: 1000,
  cleared_balance_base: 1000,
}

const TXN: ApiTxn = {
  id: 'txn-review',
  date: '2026-09-17',
  kind: 'expense',
  category: 'cat-groceries',
  payee_id: 'pay-kroger',
  description: 'Kroger #184',
  memo: 'Weekly shop',
  base_amount: 113.29,
  base_currency: 'SGD',
  cleared: false,
  needs_review: true,
  source: 'automation',
  created_at: '2026-09-18T03:00:00Z',
  from_account_id: 'acc-1',
  from_amount: 113.29,
  from_currency: 'SGD',
} as ApiTxn

/** The tab and the app-level host the surfaces live in, as the shell mounts them. */
function renderTab() {
  return render(
    <TransactionSurfacesProvider onManagePayees={() => {}}>
      <AccountsTab />
    </TransactionSurfacesProvider>,
  )
}

/** A stand-in for any caller of the surfaces — Lucius's landing, the route command. */
function OpenReview({ scope }: { scope: 'automation' | 'all' }) {
  const { openReview } = useTransactionSurfaces()
  useEffect(() => openReview(scope), [openReview, scope])
  return null
}

function setupMocks(reviewTxn: ApiTxn = TXN) {
  // Once a PUT clears the flag, the queue and the count answer with nothing — as the server
  // would. The tab's count refetches off the ledger data bus, not a local decrement.
  let reviewed = false
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (url === '/api/model-config') return Promise.resolve(okJson({ provider: 'openai' }))
    if (url === '/api/accounts') return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/accounts/')) return Promise.resolve(okJson(ACCOUNT))
    if (url.startsWith('/api/categories'))
      return Promise.resolve(
        okJson([
          { id: 'cat-groceries', label: 'Groceries', color: '#34b08a', kind: 'expense' },
          { id: 'cat-dining', label: 'Dining out', color: '#e0a032', kind: 'expense' },
        ]),
      )
    if (url.startsWith('/api/payees'))
      return Promise.resolve(okJson({ payees: [{ id: 'pay-kroger', name: 'Kroger' }] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson({ rows: [] }))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.startsWith('/api/transactions/recently-added?needs_review=true'))
      return Promise.resolve(
        reviewed
          ? okJson({ transactions: [], total: 0, has_more: false })
          : okJson({ transactions: [reviewTxn], total: 1, has_more: false }),
      )
    if (url === '/api/transactions/txn-review' && init?.method === 'PUT') {
      const update = JSON.parse(String(init.body)) as Record<string, unknown>
      if (update.needs_review === false) reviewed = true
      return Promise.resolve(okJson({ ...reviewTxn, ...update, needs_review: false }))
    }
    if (url.startsWith('/api/transactions'))
      return Promise.resolve(okJson({ transactions: [reviewTxn], total: 1, has_more: false }))
    if (url.startsWith('/api/net-worth')) return Promise.resolve(okJson({ net_worth: 886.71 }))
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  clearQueryCache()
  vi.clearAllMocks()
  setupMocks()
})

afterEach(cleanup)

describe('AccountsTab — transaction review mode', () => {
  it('opens the review in place from any caller, on automation rows only for Lucius’s door', async () => {
    render(
      <TransactionSurfacesProvider onManagePayees={() => {}}>
        <OpenReview scope="automation" />
      </TransactionSurfacesProvider>,
    )

    // No Accounts tab in the tree at all: the cards are hosted at the app level.
    expect(await screen.findByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/transactions/recently-added?needs_review=true&automation_only=true&limit=500',
      ),
    )
    expect(await screen.findByRole('heading', { name: 'Kroger' })).toBeTruthy()
  })

  it('counts and reviews everything flagged from the tab’s own row', async () => {
    renderTab()
    // The count beside "Needs review" is the count of what that filter shows: no scope.
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/transactions/recently-added?needs_review=true&limit=1',
      ),
    )
    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))
    expect(await screen.findByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/transactions/recently-added?needs_review=true&limit=500',
      ),
    )
  })

  it('explains Review in a line and clears every flag at once after one confirmation', async () => {
    let flagged = true
    const base = mockAuthFetch.getMockImplementation()!
    mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === '/api/transactions/bulk-update' && init?.method === 'POST') {
        flagged = false
        const body = JSON.parse(String(init.body)) as { ids: string[]; needs_review?: boolean }
        return Promise.resolve(
          okJson({ updated: body.ids, not_found: [], skipped: [], count: body.ids.length }),
        )
      }
      if (url.startsWith('/api/transactions/recently-added?needs_review=true'))
        return Promise.resolve(
          flagged
            ? okJson({ transactions: [TXN], total: 1, has_more: false })
            : okJson({ transactions: [], total: 0, has_more: false }),
        )
      return base(url, init)
    })
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Mark all reviewed' }))
    // A confirmation first: the flag is the user's own "I have looked at this".
    expect(await screen.findByText('Mark 1 as reviewed?')).toBeTruthy()
    expect(mockAuthFetch.mock.calls.some(([url]) => url === '/api/transactions/bulk-update')).toBe(
      false,
    )

    const buttons = screen.getAllByRole('button', { name: 'Mark all reviewed' })
    fireEvent.click(buttons[buttons.length - 1]!)
    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        ([url, init]) => url === '/api/transactions/bulk-update' && init?.method === 'POST',
      )
      expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
        ids: ['txn-review'],
        needs_review: false,
      })
    })
    await waitFor(() => expect(screen.queryByRole('button', { name: '1 to review' })).toBeNull())
  })

  it('offers Mark all reviewed only on All accounts, never on one account it would overreach', async () => {
    renderTab()
    expect(await screen.findByRole('button', { name: 'Mark all reviewed' })).toBeTruthy()
    cleanup()

    // One account's header counts that account, so a button clearing every account's flags
    // beside it would act on rows the user cannot see (review 2026-10-14).
    render(
      <TransactionSurfacesProvider onManagePayees={() => {}}>
        <AccountsTab selectedAccountId="acc-1" />
      </TransactionSurfacesProvider>,
    )
    expect(await screen.findByRole('button', { name: 'Start review' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Mark all reviewed' })).toBeNull()
  })

  it('shows the overlay at once, in its loading state, while the book is still loading', async () => {
    // Every request stays pending.
    mockAuthFetch.mockImplementation(() => new Promise(() => {}))
    render(
      <TransactionSurfacesProvider onManagePayees={() => {}}>
        <OpenReview scope="automation" />
      </TransactionSurfacesProvider>,
    )

    const dialog = await screen.findByRole('dialog', { name: 'Review transactions' })
    expect(within(dialog).getByText('Loading transactions…')).toBeTruthy()
    // Cards wait for the accounts, categories and payees too: never a card against an empty book.
    expect(within(dialog).queryByTestId('transaction-review-card')).toBeNull()
  })

  it('filters the register on the count and opens the card flow only from Start review', async () => {
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: '1 to review' }))
    expect(await screen.findByText('Needs review')).toBeTruthy()
    expect(screen.queryByRole('dialog', { name: 'Review transactions' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Start review' }))
    expect(await screen.findByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
  })

  it('opens the compact queue and approves the card draft atomically without navigating away', async () => {
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))
    expect(await screen.findByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Kroger' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Ask Lucius' })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Other' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dining out' }))
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        ([url, init]) => url === '/api/transactions/txn-review' && init?.method === 'PUT',
      )
      expect(call).toBeTruthy()
      const update = JSON.parse(String(call?.[1]?.body)) as Record<string, unknown>
      expect(update).toEqual({
        category: 'cat-dining',
        needs_review: false,
      })
      expect(update).not.toHaveProperty('payee_id')
    })

    expect(await screen.findByText('You’re all caught up')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Done' }))
    expect(screen.queryByRole('button', { name: '1 to review' })).toBeNull()
  })

  it('closes the drawer on save when it was opened straight into edit from a card', async () => {
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))
    const save = await screen.findByRole('button', { name: 'Save changes' })

    fireEvent.click(save)
    await waitFor(() => expect(document.querySelector('.acct-detail-wrap')).toBeNull())
    // Back on the review card, not stranded on a detail view.
    expect(screen.getByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
  })

  it('does not create a payee from an untouched imported description', async () => {
    setupMocks({ ...TXN, payee_id: null, description: 'RAW BANK MERCHANT 184', memo: '' })
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))
    // The bank string identifies the row in the details; the heading says no payee is linked.
    expect(await screen.findByRole('heading', { name: 'No payee yet' })).toBeTruthy()
    expect(
      within(screen.getByTestId('transaction-review-card')).getByText('RAW BANK MERCHANT 184'),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => {
      const call = mockAuthFetch.mock.calls.find(
        ([url, init]) => url === '/api/transactions/txn-review' && init?.method === 'PUT',
      )
      expect(call).toBeTruthy()
      expect(JSON.parse(String(call?.[1]?.body))).not.toHaveProperty('payee_id')
    })
    expect(
      mockAuthFetch.mock.calls.some(
        ([url, init]) => url === '/api/payees' && init?.method === 'POST',
      ),
    ).toBe(false)
  })

  it('opens the full editor already editing, lifted above the review modal', async () => {
    renderTab()

    fireEvent.click(await screen.findByRole('button', { name: 'Start review' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit' }))

    await waitFor(() =>
      expect(document.querySelector('.acct-detail-wrap.above-modal')).toBeTruthy(),
    )
    expect(await screen.findByRole('button', { name: 'Save changes' })).toBeTruthy()
    expect(screen.getByRole('dialog', { name: 'Review transactions' })).toBeTruthy()
  })
})
