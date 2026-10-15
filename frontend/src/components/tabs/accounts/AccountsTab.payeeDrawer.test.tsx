// @vitest-environment jsdom
//
// PAYEE1 regression: the transaction detail drawer's Payee combobox
// (AddPayeeField, pickers.tsx) shows a "+ Create '<name>'" suggestion for an
// unrecognized name, but selecting it used to just commit the typed STRING into
// form state — saveDetailEdit then ran that string through resolvePayeeId (a
// client-side NAME lookup against the already-loaded payees), which returns null
// for a brand-new name, so the payee silently never got linked. Investigation
// (see backend/tests/test_update_transaction_payee_note.py) confirmed
// update_transaction never auto-resolves/creates a payee from note text the way
// create_transaction does — so the fix cannot "ride the note" either; it must
// explicitly create the payee (POST /api/payees) and await the real id.
//
// Also covers the lower-priority UX gap on the same save path: staying in Edit mode
// (never silently flipping to View) until the save request actually resolves. The
// success toast it once expected is gone — the transaction surfaces show no toast bar
// (owner, 2026-09-28).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import { useEffect } from 'react'
import { TransactionSurfacesProvider } from './TransactionSurfaces'
import { useTransactionSurfaces } from './transactionSurfacesContext'
import { fromApiTxn } from './data'
import { clearQueryCache } from '../../../state/queryCache'
import type { ApiAccount, ApiPayee, ApiTxn } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body, text: async () => JSON.stringify(body) } as Response
}

const ACCOUNT: ApiAccount = {
  id: 'acc-1',
  name: 'Checking',
  kind: 'asset',
  type: 'cash',
  currency: 'USD',
  balance: 1000,
  balance_base: 1000,
  cleared_balance: 1000,
  cleared_balance_base: 1000,
}

const EXISTING_PAYEE: ApiPayee = { id: 'pay_existing1', name: 'Groceries Store' }

const TXN: ApiTxn = {
  id: 'txn-1',
  date: '2026-06-01',
  kind: 'expense',
  category: 'food',
  description: 'Original description text',
  memo: '',
  payee_id: null,
  base_amount: 42,
  base_currency: 'USD',
  cleared: true,
  from_account_id: 'acc-1',
  from_amount: 42,
  from_currency: 'USD',
}

function calls(
  urlPrefix: string,
): Array<{ method?: string; url: string; body: Record<string, unknown> | undefined }> {
  return mockAuthFetch.mock.calls
    .filter(([u]) => String(u).startsWith(urlPrefix))
    .map(([u, opts]) => ({
      url: String(u),
      method: opts?.method,
      body: opts?.body ? JSON.parse(String(opts.body)) : undefined,
    }))
}

function setupMocks() {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url === '/api/payees' && opts?.method === 'POST') {
      const { name } = JSON.parse(String(opts.body))
      return Promise.resolve(
        okJson({ id: 'pay_new123', name, aliases: [], default_categories: {} }),
      )
    }
    if (url === '/api/payees') return Promise.resolve(okJson({ payees: [EXISTING_PAYEE] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url === `/api/transactions/${TXN.id}/activity`) {
      return Promise.resolve(
        okJson({ addedBy: 'You', createdAt: TXN.date, updatedAt: TXN.date, activity: [] }),
      )
    }
    if (url === `/api/transactions/${TXN.id}` && opts?.method === 'PUT') {
      const patch = JSON.parse(String(opts.body))
      return Promise.resolve(okJson({ ...TXN, ...patch }))
    }
    if (url.startsWith('/api/transactions')) {
      return Promise.resolve(okJson({ transactions: [TXN], total: 1, has_more: false }))
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  // The query cache is module-level: without this, a URL fetched by an earlier
  // test is served from cache here and never reaches the authFetch mock.
  clearQueryCache()
  vi.clearAllMocks()
  setupMocks()
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 1400 })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** What a deep link does now: asks the app-level host to open the drawer for the row. */
function OpenTxn() {
  const { openTransaction } = useTransactionSurfaces()
  useEffect(() => openTransaction(fromApiTxn(TXN)), [openTransaction])
  return null
}

async function openDrawerInEditMode() {
  render(
    <TransactionSurfacesProvider onManagePayees={() => {}}>
      <AccountsTab />
      <OpenTxn />
    </TransactionSurfacesProvider>,
  )
  await screen.findByText('Edit transaction')
  // Let the payees fetch land before interacting, so resolvePayeeId sees the real list.
  await waitFor(() => expect(calls('/api/payees').some((c) => c.method !== 'POST')).toBe(true))
  fireEvent.click(screen.getByText('Edit transaction'))
  await screen.findByPlaceholderText('Payee')
}

function createSuggestionFor(name: string): HTMLElement {
  const item = Array.from(document.querySelectorAll('.acct-payf-item.create')).find((el) =>
    el.textContent?.includes(name),
  )
  if (!item) throw new Error(`no "+ Create" suggestion found for ${name}`)
  return item as HTMLElement
}

describe('Transaction detail drawer — payee creation on save (PAYEE1)', () => {
  it('creates a brand-new payee and preserves the memo typed in the same save', async () => {
    await openDrawerInEditMode()

    const payeeInput = screen.getByPlaceholderText('Payee')
    fireEvent.change(payeeInput, { target: { value: 'Cousin - A' } })
    fireEvent.mouseDown(createSuggestionFor('Cousin - A'))

    const memoBox = screen.getByPlaceholderText('e.g. 2 lattes + croissant')
    fireEvent.change(memoBox, { target: { value: 'Reimbursement for dinner' } })

    fireEvent.click(screen.getByText('Save changes'))

    // The payee is explicitly created server-side — never silently dropped, never
    // ridden into the description text.
    await waitFor(() => expect(calls('/api/payees').some((c) => c.method === 'POST')).toBe(true))
    const createCall = calls('/api/payees').find((c) => c.method === 'POST')!
    expect(createCall.body).toEqual({ name: 'Cousin - A' })

    // The transaction is saved with the REAL created id — and the user's own memo
    // text survives untouched alongside it (no clobbering).
    await waitFor(() =>
      expect(calls(`/api/transactions/${TXN.id}`).some((c) => c.method === 'PUT')).toBe(true),
    )
    const putCall = calls(`/api/transactions/${TXN.id}`).find((c) => c.method === 'PUT')!
    expect(putCall.body!.payee_id).toBe('pay_new123')
    expect(putCall.body!.memo).toBe('Reimbursement for dinner')

    // The drawer only flips back to View once the save has actually resolved: View's own
    // "Edit transaction" BUTTON (in Edit the same words are the heading) — and no toast.
    expect(await screen.findByRole('button', { name: 'Edit transaction' })).toBeTruthy()
    expect(document.querySelector('.acct-toast')).toBeNull()
  })

  it('resolves an EXISTING payee straight to its id — no create call, unchanged from today', async () => {
    await openDrawerInEditMode()

    const payeeInput = screen.getByPlaceholderText('Payee')
    fireEvent.change(payeeInput, { target: { value: EXISTING_PAYEE.name } })
    fireEvent.mouseDown(screen.getByText(EXISTING_PAYEE.name).closest('button')!)

    fireEvent.click(screen.getByText('Save changes'))

    await waitFor(() =>
      expect(calls(`/api/transactions/${TXN.id}`).some((c) => c.method === 'PUT')).toBe(true),
    )
    const putCall = calls(`/api/transactions/${TXN.id}`).find((c) => c.method === 'PUT')!
    expect(putCall.body!.payee_id).toBe(EXISTING_PAYEE.id)

    // No new payee is ever minted for a name that already resolves.
    expect(calls('/api/payees').some((c) => c.method === 'POST')).toBe(false)
  })

  it('stays in Edit mode (never silently proceeds) when the save request fails', async () => {
    await openDrawerInEditMode()

    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      if (url === `/api/transactions/${TXN.id}` && opts?.method === 'PUT') {
        return Promise.resolve({ ok: false, text: async () => 'boom' } as Response)
      }
      if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
      if (url === '/api/payees') return Promise.resolve(okJson({ payees: [EXISTING_PAYEE] }))
      if (url === `/api/transactions/${TXN.id}/activity`) {
        return Promise.resolve(
          okJson({ addedBy: 'You', createdAt: TXN.date, updatedAt: TXN.date, activity: [] }),
        )
      }
      return Promise.resolve(okJson({}))
    })

    const memoBox = screen.getByPlaceholderText('e.g. 2 lattes + croissant')
    fireEvent.change(memoBox, { target: { value: 'This will fail to save' } })
    await act(async () => {
      fireEvent.click(screen.getByText('Save changes'))
    })

    await screen.findByText(/update failed|boom/i)
    // Still in Edit — the drawer never silently flips to View on a failed save.
    expect(screen.getByText('Save changes')).toBeTruthy()
  })
})

describe('Inline register-cell payee edit (updateFromPatch, PAYEE1 follow-up)', () => {
  it('creates a brand-new payee and never clobbers the description (unlike the old note-riding fallback)', async () => {
    render(<AccountsTab />)
    await waitFor(() => expect(calls('/api/payees').some((c) => c.method !== 'POST')).toBe(true))

    const payeeCell = await waitFor(() => {
      const el = document.querySelector('.acct-td-payee')
      if (!el) throw new Error('payee cell not rendered yet')
      return el as HTMLElement
    })
    fireEvent.doubleClick(payeeCell)

    const payeeInput = await screen.findByPlaceholderText('Payee')
    fireEvent.change(payeeInput, { target: { value: 'Cousin - A' } })
    fireEvent.mouseDown(createSuggestionFor('Cousin - A'))

    await waitFor(() => expect(calls('/api/payees').some((c) => c.method === 'POST')).toBe(true))
    expect(calls('/api/payees').find((c) => c.method === 'POST')!.body).toEqual({
      name: 'Cousin - A',
    })

    await waitFor(() =>
      expect(calls(`/api/transactions/${TXN.id}`).some((c) => c.method === 'PUT')).toBe(true),
    )
    const putCall = calls(`/api/transactions/${TXN.id}`).find((c) => c.method === 'PUT')!
    expect(putCall.body!.payee_id).toBe('pay_new123')
    // The inline cell edit only ever touches `payee` — it must never ALSO stamp `description`
    // (the old note-riding fallback wrote `u.note = patch.payee`, silently overwriting
    // whatever description the transaction already had).
    expect(putCall.body!.description).toBeUndefined()
  })
})
