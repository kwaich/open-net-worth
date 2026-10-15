// @vitest-environment jsdom
//
// Regression: a kind=transfer transaction MAY carry an optional category (owner decision
// 2026-07-03), but the detail drawer's category picker filtered to `catOptionsFor('expense', …)`
// unconditionally — a transfer could never be labeled with its own TRANSFER-kind category.
// Categories are KIND-SCOPED (expense|income|transfer|investment — see Manage categories), so the
// picker must offer the categories whose kind MATCHES the transaction: a transfer shows ONLY
// transfer-kind categories (grouped "Transfer"), never income/expense ones (owner, 2026-07-10).

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen, fireEvent, within } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'From Account',
    kind: 'asset',
    type: 'cash',
    institution: '',
    currency: 'SGD',
    initialBalance: 0,
    initialBalanceDate: null,
    creditLimit: null,
    lastFour: null,
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
    balance: 0,
    balanceBase: 0,
    clearedBalance: 0,
    clearedBalanceBase: 0,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

const FROM = makeAccount({ id: 'a1', name: 'Checking', currency: 'SGD' })
const TO = makeAccount({ id: 'a2', name: 'Savings', currency: 'SGD', type: 'cash' })

function makeTransferTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-01',
    kind: 'transfer',
    category: null,
    payeeId: null,
    payeeName: null,
    description: '',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 100,
    fromCurrency: 'SGD',
    toAccountId: 'a2',
    toAmount: 100,
    toCurrency: 'SGD',
    origAmount: null,
    origCurrency: null,
    baseAmount: 100,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-07-01',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

const cats: Category[] = [
  { id: 'inc1', label: 'Salary', color: '#3b86dd', kind: 'income', group: 'Income' },
  { id: 'exp1', label: 'Groceries', color: '#e0a032', kind: 'expense', group: 'Needs' },
  { id: 'xfer1', label: 'Global City Apartment', color: '#e05a8a', kind: 'transfer', group: '' },
]

function renderEditingDrawer(t: Txn) {
  render(
    <TxnDetail
      txn={t}
      accounts={[FROM, TO]}
      categories={cats}
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
          createdAt: '2026-07-01',
          updatedAt: '2026-07-01',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
  fireEvent.click(screen.getByText('Edit transaction'))
}

describe('TxnDetail — transfer category picker is kind-scoped to transfer categories', () => {
  it('shows only transfer-kind categories (grouped "Transfer") when opened for a transfer', () => {
    renderEditingDrawer(makeTransferTxn())
    fireEvent.click(screen.getByText('Category…'))
    const listbox = within(screen.getByRole('listbox'))
    expect(listbox.getByText('Transfer')).toBeTruthy() // the group header
    expect(listbox.getByText('Global City Apartment')).toBeTruthy() // the transfer-kind category
  })

  it('does NOT offer income or expense categories on a transfer (categories are kind-scoped)', () => {
    renderEditingDrawer(makeTransferTxn())
    fireEvent.click(screen.getByText('Category…'))
    const listbox = within(screen.getByRole('listbox'))
    expect(listbox.queryByText('Salary')).toBeNull() // income category — not offered
    expect(listbox.queryByText('Groceries')).toBeNull() // expense category — not offered
    expect(listbox.queryByText('Income')).toBeNull()
    expect(listbox.queryByText('Expenses')).toBeNull()
  })
})
