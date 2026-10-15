// @vitest-environment jsdom
//
// Regression: editing a same-currency transfer's Amount field in the detail drawer only
// updated from_amount — to_amount silently kept its ORIGINAL value. The "Received (ccy)"
// field that seeds `toAmount` only renders when the from/to accounts have DIFFERENT
// currencies (EditBody, TxnDetail.tsx); when they match, that field is hidden but its
// state still starts pre-filled with the transaction's original to_amount and `save()`
// unconditionally sent it as `patch.toAmount`, clobbering to_amount with a stale number
// on every same-currency transfer edit (an edited amount of 6300.00 left to_amount at the
// old 6400.00).

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'From Account',
    kind: 'asset',
    type: 'investment',
    institution: '',
    currency: 'EUR',
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

const FROM = makeAccount({ id: 'a1', name: 'Crestmark Workplace Solutions', currency: 'EUR' })
const TO = makeAccount({
  id: 'a2',
  name: 'Meridian Multiplier (EUR)',
  currency: 'EUR',
  type: 'cash',
})

function makeTransferTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-16',
    kind: 'transfer',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'ONWAI sale proceeds transfer to Meridian',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 6400.0,
    fromCurrency: 'EUR',
    toAccountId: 'a2',
    toAmount: 6400.0,
    toCurrency: 'EUR',
    origAmount: null,
    origCurrency: null,
    baseAmount: 6400.0,
    baseCurrency: 'EUR',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-16',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderDrawer2(
  accounts: Account[],
  t: Txn,
  onSave: (id: string, patch: Record<string, unknown>) => void,
) {
  return render(
    <TxnDetail
      txn={t}
      accounts={accounts}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="EUR"
      rates={{}}
      onClose={() => {}}
      onSave={onSave}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() =>
        Promise.resolve({
          source: 'manual',
          addedBy: 'You',
          createdAt: '2026-06-16',
          updatedAt: '2026-06-16',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}
function renderDrawer(t: Txn, onSave: (id: string, patch: Record<string, unknown>) => void) {
  return renderDrawer2([FROM, TO], t, onSave)
}

describe('TxnDetail — editing a same-currency transfer amount', () => {
  it('sends the new amount as BOTH fromAmount and toAmount, not the stale original toAmount', () => {
    let savedPatch: Record<string, unknown> | null = null
    renderDrawer(makeTransferTxn(), (_id, patch) => {
      savedPatch = patch
    })

    fireEvent.click(screen.getByText('Edit transaction'))
    const amountInput = document.querySelector('.acct-edit-amt-in') as HTMLInputElement
    expect(amountInput).toBeTruthy()
    fireEvent.change(amountInput, { target: { value: '6300.00' } })
    fireEvent.click(screen.getByText('Save changes'))

    expect(savedPatch).not.toBeNull()
    expect((savedPatch as unknown as Record<string, unknown>).amount).toBe(6300.0)
    expect((savedPatch as unknown as Record<string, unknown>).toAmount).toBe(6300.0)
  })

  it('cross-currency transfer still honors an explicit Received amount', () => {
    let savedPatch: Record<string, unknown> | null = null
    const txn = makeTransferTxn({
      toAccountId: 'a2',
      toCurrency: 'USD',
      toAmount: 100,
      fromAmount: 90,
      fromCurrency: 'EUR',
    })
    render(
      <TxnDetail
        txn={txn}
        accounts={[FROM, makeAccount({ id: 'a2', name: 'USD Wallet', currency: 'USD' })]}
        categories={[]}
        payeeOptions={[]}
        baseCurrency="EUR"
        rates={{}}
        onClose={() => {}}
        onSave={(_id, patch) => {
          savedPatch = patch
        }}
        onDelete={() => {}}
        onMarkReviewed={() => {}}
        onManagePayees={() => {}}
        onCreateCategory={() => {}}
        onLoadActivity={() =>
          Promise.resolve({
            source: 'manual',
            addedBy: 'You',
            createdAt: '2026-06-16',
            updatedAt: '2026-06-16',
            activity: [],
          })
        }
        onLoadSourceEntries={() => Promise.resolve(null)}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    fireEvent.click(screen.getByText('Edit transaction'))
    fireEvent.click(screen.getByText('Save changes'))

    expect(savedPatch).not.toBeNull()
    // Unchanged Received field still carries its original (different-currency) value through.
    expect((savedPatch as unknown as Record<string, unknown>).toAmount).toBe(100)
  })

  it('switching the To account to a different currency mid-edit resets the newly-revealed Received field, not carrying over the old same-currency number', () => {
    let savedPatch: Record<string, unknown> | null = null
    const USD_WALLET = makeAccount({ id: 'a3', name: 'Some USD Wallet', currency: 'USD' })
    renderDrawer2([FROM, TO, USD_WALLET], makeTransferTxn(), (_id, patch) => {
      savedPatch = patch
    })

    fireEvent.click(screen.getByText('Edit transaction'))
    // Change the main Amount first — while To is still same-currency (EUR), the Received
    // field stays hidden, so this new value only lives in f.amount so far.
    const amountInput = document.querySelector('.acct-edit-amt-in') as HTMLInputElement
    fireEvent.change(amountInput, { target: { value: '150' } })

    // Now switch the To account to a USD one — this reveals "Received (USD)".
    fireEvent.click(screen.getByText('Meridian Multiplier (EUR)'))
    fireEvent.mouseDown(screen.getByText('Some USD Wallet'))

    // Save WITHOUT touching the newly-revealed Received field.
    fireEvent.click(screen.getByText('Save changes'))

    expect(savedPatch).not.toBeNull()
    // Must mirror the freshly-typed amount (150), never the stale original EUR to_amount (6400.0).
    expect((savedPatch as unknown as Record<string, unknown>).toAmount).toBe(150)
  })
})
