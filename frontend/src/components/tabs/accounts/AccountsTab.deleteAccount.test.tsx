// @vitest-environment jsdom
//
// "Delete account" — the menu item next to Archive that DID NOT exist before this change, plus
// its two-step escalation: a plain confirm first, and only on a 409 (real transaction history
// beyond the account's own opening balance) a SECOND explicit confirm that sends force=true.
// Force must never fire off the first confirm alone.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { AccountsTab } from './AccountsTab'
import type { ApiAccount } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown, status = 200): Response {
  return { ok: true, status, json: async () => body } as Response
}
function errJson(status: number, body: unknown): Response {
  return {
    ok: false,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response
}

const ACCOUNT: ApiAccount = {
  id: 'acc-1',
  name: 'Old Savings',
  kind: 'asset',
  type: 'cash',
  currency: 'USD',
  balance: 0,
  balance_base: 0,
  cleared_balance: 0,
  cleared_balance_base: 0,
}

const ARCHIVED_ACCOUNT: ApiAccount = {
  ...ACCOUNT,
  id: 'acc-archived',
  name: 'Closed Savings',
  archived: true,
}

const ARCHIVED_TXN = {
  id: 'txn-archived',
  date: '2026-09-01',
  kind: 'expense',
  description: 'Archived transaction',
  from_account_id: ARCHIVED_ACCOUNT.id,
  from_amount: 42,
  from_currency: 'USD',
  base_amount: 42,
  base_currency: 'USD',
  cleared: true,
}

/** DELETE calls the mock has seen, in order — `{url, force}`. */
let deleteCalls: Array<{ url: string; force: boolean }> = []
let deleteOutcome: (force: boolean) => Response

function setupMocks() {
  deleteCalls = []
  mockAuthFetch.mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE' && url.startsWith('/api/accounts/')) {
      const force = url.includes('force=true')
      deleteCalls.push({ url, force })
      return Promise.resolve(deleteOutcome(force))
    }
    if (init?.method === 'PUT' && url === '/api/accounts/acc-archived') {
      return Promise.resolve(okJson({ ...ARCHIVED_ACCOUNT, archived: false }))
    }
    if (url === '/api/accounts?include_archived=true')
      return Promise.resolve(okJson([ACCOUNT, ARCHIVED_ACCOUNT]))
    if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([ACCOUNT]))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
    if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
    if (url.includes('account_id=acc-archived'))
      return Promise.resolve(okJson({ transactions: [ARCHIVED_TXN], total: 1, has_more: false }))
    if (url.startsWith('/api/transactions'))
      return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => vi.clearAllMocks())
afterEach(() => cleanup())

async function boot() {
  setupMocks()
  render(<AccountsTab selectedAccountId={ACCOUNT.id} />)
  await waitFor(() => expect(screen.getByLabelText('More actions')).toBeTruthy())
}

const openMenu = () => fireEvent.click(screen.getByLabelText('More actions'))
const deleteMenuItem = () => screen.getByRole('button', { name: /Delete account/i })

describe('Accounts — Delete account menu item + two-step escalation', () => {
  it('opens the archived-account browser from the all-accounts kebab menu', async () => {
    setupMocks()
    render(<AccountsTab selectedAccountId={null} />)
    await waitFor(() => expect(screen.getByLabelText('More actions')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('More actions'))
    fireEvent.click(screen.getByRole('button', { name: /Archived accounts/i }))

    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/accounts?include_archived=true'),
    )
    expect(await screen.findByText('Closed Savings')).toBeTruthy()
  })

  it('shows an archived account transaction list without restoring it', async () => {
    setupMocks()
    render(<AccountsTab selectedAccountId={null} />)
    await waitFor(() => expect(screen.getByLabelText('More actions')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('More actions'))
    fireEvent.click(screen.getByRole('button', { name: /Archived accounts/i }))
    fireEvent.click(await screen.findByRole('button', { name: /Closed Savings/i }))

    expect(await screen.findByText('Archived transaction')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Add transaction/i })).toBeNull()
  })

  it('restores an archived account from its read-only history', async () => {
    setupMocks()
    render(<AccountsTab selectedAccountId={null} />)
    await waitFor(() => expect(screen.getByLabelText('More actions')).toBeTruthy())

    fireEvent.click(screen.getByLabelText('More actions'))
    fireEvent.click(screen.getByRole('button', { name: /Archived accounts/i }))
    fireEvent.click(await screen.findByRole('button', { name: /Closed Savings/i }))
    fireEvent.click(screen.getByRole('button', { name: /Restore account/i }))

    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith('/api/accounts/acc-archived', {
        method: 'PUT',
        body: JSON.stringify({ archived: false }),
      }),
    )
    expect(await screen.findByText('No archived accounts.')).toBeTruthy()
  })

  it('shows a Delete account item next to Archive in the account menu', async () => {
    await boot()
    openMenu()
    expect(screen.getByRole('button', { name: /Archive account/i })).toBeTruthy()
    expect(deleteMenuItem()).toBeTruthy()
  })

  it('deletes outright on the first confirm when the account has no blocking history', async () => {
    deleteOutcome = () => okJson({ ok: true })
    await boot()
    openMenu()
    fireEvent.click(deleteMenuItem())

    // First (and only) confirm dialog.
    const dialog = screen.getByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: /^Delete account$/ }))

    await waitFor(() => expect(deleteCalls.length).toBe(1))
    expect(deleteCalls[0]!.force).toBe(false)
    // No second dialog — the escalation never fires on a clean delete.
    expect(screen.queryByText(/Force delete/i)).toBeNull()
  })

  it('escalates to a SECOND confirm on a 409, and only sends force=true after that second confirm', async () => {
    deleteOutcome = (force) =>
      force
        ? okJson({ ok: true })
        : errJson(409, {
            detail: {
              error: 'conflict',
              message:
                "Account 'Old Savings' still has 3 transaction(s) — archive it instead " +
                '(PUT /api/accounts/{id} with archived=true) to preserve the history, or pass ' +
                'force=true to delete it and all 3 transaction(s) together',
              transaction_count: 3,
              other_accounts: [],
            },
          })
    await boot()
    openMenu()
    fireEvent.click(deleteMenuItem())

    const first = screen.getByRole('dialog')
    fireEvent.click(within(first).getByRole('button', { name: /^Delete account$/ }))

    // The 409 lands and escalates — a SECOND explicit confirm, never auto-escalated.
    await waitFor(() => expect(deleteCalls.length).toBe(1))
    expect(deleteCalls[0]!.force).toBe(false)

    const second = await screen.findByRole('dialog')
    expect(within(second).getAllByText(/3 transaction\(s\)/).length).toBeGreaterThan(0)
    const forceButton = within(second).getByRole('button', {
      name: /Force delete and remove 3 transaction\(s\)/i,
    })

    // Still no force call until this second dialog is itself confirmed.
    expect(deleteCalls.length).toBe(1)

    fireEvent.click(forceButton)

    await waitFor(() => expect(deleteCalls.length).toBe(2))
    expect(deleteCalls[1]!.force).toBe(true)
  })

  it('reads the count from the STRUCTURED error body, never regexed prose — a name containing a number does not corrupt it', async () => {
    // Regression for security review MEDIUM-3: an account named "Fund 1 transaction reserve"
    // used to break a `/(\d+)\s+transaction/` regex parse of the message (it matched the "1"
    // in the NAME, not the real count of 47). The structured `transaction_count` field is
    // immune to this because it never touches the prose at all.
    deleteOutcome = () =>
      errJson(409, {
        detail: {
          error: 'conflict',
          message:
            "Account 'Fund 1 transaction reserve' still has 47 transaction(s) — archive it " +
            'instead (PUT /api/accounts/{id} with archived=true) to preserve the history, or ' +
            'pass force=true to delete it and all 47 transaction(s) together',
          transaction_count: 47,
          other_accounts: [],
        },
      })
    await boot()
    openMenu()
    fireEvent.click(deleteMenuItem())
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /^Delete account$/ }),
    )

    const second = await screen.findByRole('dialog')
    expect(
      within(second).getByRole('button', { name: /Force delete and remove 47 transaction\(s\)/i }),
    ).toBeTruthy()
    // The corrupted-by-regex reading ("1 transaction") must never appear as the offered count.
    expect(within(second).queryByRole('button', { name: /remove 1 transaction/i })).toBeNull()
  })

  it('names the other account(s) a shared transfer also affects in the escalated confirm', async () => {
    deleteOutcome = () =>
      errJson(409, {
        detail: {
          error: 'conflict',
          message: "Account 'Old Savings' still has 2 transaction(s)",
          transaction_count: 2,
          other_accounts: [{ id: 'acc-2', name: 'Meridian Bank Savings' }],
        },
      })
    await boot()
    openMenu()
    fireEvent.click(deleteMenuItem())
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /^Delete account$/ }),
    )

    const second = await screen.findByRole('dialog')
    expect(within(second).getByText(/Meridian Bank Savings/)).toBeTruthy()
  })

  it('never offers a force-delete escalation for a net-worth-linked refusal (a linked loan force=true cannot fix)', async () => {
    // Regression for the second security-review pass: an account blocked ONLY by a linked
    // mortgage (zero transactions) used to still show "Force delete and remove your
    // transactions" — a button that would just 409 again, since force=true has nothing to do
    // with an owned_assets.loan_account_id link.
    deleteOutcome = () =>
      errJson(409, {
        detail: {
          error: 'account_net_worth_linked',
          message:
            "Account 'Old Savings' is linked to the loan on Lakeside House — unlink it " +
            "(clear the asset's loan account) before deleting this account",
          linked_asset_ids: ['oa-1'],
          open_position_count: 0,
          open_lot_count: 0,
          open_option_count: 0,
        },
      })
    await boot()
    openMenu()
    fireEvent.click(deleteMenuItem())
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /^Delete account$/ }),
    )

    // No second dialog at all — this refusal has no force-delete escalation.
    await waitFor(() => expect(deleteCalls.length).toBe(1))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(screen.queryByText(/Force delete/i)).toBeNull()
  })
})
