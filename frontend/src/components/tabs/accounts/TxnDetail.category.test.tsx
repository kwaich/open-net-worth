// @vitest-environment jsdom
//
// Regression: the transaction detail drawer resolved category labels/colors
// against the hardcoded built-in CAT map ("dining", "groceries", …) instead of
// the live `categories` prop. Real user categories (e.g. "food") aren't in that
// static list, so the drawer rendered them as "Uncategorised" even though the
// register row showed the category. The drawer must resolve against the live
// categories that are already passed into it.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen, waitFor } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Northgate Rewards Card',
    kind: 'asset',
    type: 'credit',
    institution: 'Northgate',
    currency: 'SGD',
    initialBalance: 0,
    initialBalanceDate: null,
    creditLimit: null,
    lastFour: '1234',
    note: '',
    statementCloseDay: null,
    paymentDueDay: null,
    reminderDays: null,
    lastReconciled: null,
    archived: false,
    connected: false,
    apr: null,
    principal: null,
    payoffDate: null,
    minPayment: null,
    apy: null,
    balance: -100,
    balanceBase: -100,
    clearedBalance: -100,
    clearedBalanceBase: -100,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-17',
    kind: 'expense',
    category: 'food',
    payeeId: null,
    payeeName: null,
    description: 'Test',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 28.18,
    fromCurrency: 'SGD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 28.18,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-17',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

// A user category whose key ("food") is NOT in the hardcoded built-in CAT list.
const liveCats: Category[] = [
  { id: 'food', label: 'Food & Dining', color: '#e0a032', kind: 'expense', group: 'Needs' },
]

function renderDrawer(t: Txn, categories: Category[]) {
  return render(
    <TxnDetail
      txn={t}
      accounts={[makeAccount()]}
      categories={categories}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={{}}
      onClose={() => {}}
      onSave={() => {}}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() =>
        Promise.resolve({
          source: 'manual',
          addedBy: 'You',
          createdAt: '2026-06-17',
          updatedAt: '2026-06-17',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

describe('TxnDetail — category resolves against the live categories prop', () => {
  it('renders a live (non-builtin) category by its label, not "Uncategorized"', () => {
    renderDrawer(makeTxn(), liveCats)
    expect(screen.getByText('Food & Dining')).toBeTruthy()
    expect(screen.queryByText('Uncategorized')).toBeNull()
  })

  it('falls back to "Uncategorized" only when the category is genuinely absent (expense/income)', () => {
    renderDrawer(makeTxn({ category: null }), liveCats)
    expect(screen.getByText('Uncategorized')).toBeTruthy()
  })
})

describe('TxnDetail — activity diff resolves ids to names', () => {
  const cats: Category[] = [
    { id: 'food', label: 'Food & Dining', color: '#e0a032', kind: 'expense', group: 'Needs' },
    {
      id: 'uncategorized',
      label: 'Uncategorized',
      color: '#646c7c',
      kind: 'expense',
      group: 'Others',
    },
  ]
  const activity = {
    source: 'ai',
    addedBy: 'Lucius',
    createdAt: '2026-06-27T03:56:00Z',
    updatedAt: '2026-06-27T04:00:00Z',
    activity: [
      {
        action: 'updated',
        actor: 'lucius',
        actorName: 'Lucius',
        at: '2026-06-27T03:57:00Z',
        changes: [
          { field: 'payee_id', from: 'pay_known', to: 'pay_deleted' },
          { field: 'category', from: 'uncategorized', to: 'food' },
          { field: 'base_amount', from: 1, to: 2 },
        ],
      },
    ],
  }
  function renderWithActivity() {
    return render(
      <TxnDetail
        txn={makeTxn()}
        accounts={[makeAccount()]}
        categories={cats}
        payeeOptions={[{ value: 'Alex Tan', label: 'Alex Tan' }]}
        payeeById={{ pay_known: 'Alex Tan' }}
        baseCurrency="SGD"
        rates={{}}
        onClose={() => {}}
        onSave={() => {}}
        onDelete={() => {}}
        onMarkReviewed={() => {}}
        onManagePayees={() => {}}
        onCreateCategory={() => {}}
        onLoadActivity={() => Promise.resolve(activity)}
        onLoadSourceEntries={() => Promise.resolve(null)}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
  }

  it('resolves a known payee id to its name', async () => {
    renderWithActivity()
    await waitFor(() => expect(screen.getByText('Alex Tan')).toBeTruthy())
  })

  it('falls back to the raw id for a deleted payee', async () => {
    renderWithActivity()
    await waitFor(() => expect(screen.getByText('pay_deleted')).toBeTruthy())
  })

  it('resolves category keys to labels and hides internal fields (base_amount)', async () => {
    renderWithActivity()
    await waitFor(() => expect(screen.getByText('Food & Dining')).toBeTruthy())
    expect(screen.getByText('Uncategorized')).toBeTruthy()
    expect(screen.queryByText('Base amount')).toBeNull()
  })
})
