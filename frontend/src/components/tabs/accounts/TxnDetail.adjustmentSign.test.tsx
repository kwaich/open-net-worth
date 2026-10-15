// @vitest-environment jsdom
//
// A Balance adjustment carries its direction in ONE leg, so the drawer must show its sign.
// Regression: a Starting Balance of -S$1,000.00 rendered as "S$1,000.00" in the
// drawer while the register showed it negative — the sign prefix skipped adjustments along
// with transfers, where a single sign genuinely does mean nothing.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

afterEach(cleanup)

const ACCT = {
  id: 'a1',
  name: 'Nimbus',
  kind: 'asset',
  type: 'cash',
  institution: '',
  currency: 'SGD',
  initialBalance: 0,
  initialBalanceDate: null,
  creditLimit: null,
  lastFour: null,
  color: '#22d3ee',
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
  role: null,
  holdingsBase: 0,
} as unknown as Account

function opening(over: Partial<Txn>): Txn {
  return {
    id: 'txn_open',
    date: '2026-05-06',
    kind: 'adjustment',
    category: null,
    payeeId: null,
    description: 'Starting Balance',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: null,
    fromAmount: null,
    fromCurrency: null,
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 1000,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-05-06',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  } as Txn
}

function show(t: Txn) {
  render(
    <TxnDetail
      txn={t}
      accounts={[ACCT]}
      categories={[]}
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
          createdAt: '2026-05-06',
          updatedAt: '2026-05-06',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

describe('TxnDetail — a Balance adjustment shows its direction', () => {
  it('a DECREASE (from-leg) renders negative', () => {
    show(opening({ fromAccountId: 'a1', fromAmount: 1000, fromCurrency: 'SGD' }))
    expect(screen.getByText(/−S\$1,000\.00/)).toBeTruthy()
  })

  it('an INCREASE (to-leg) renders positive', () => {
    show(opening({ toAccountId: 'a1', toAmount: 1000, toCurrency: 'SGD' }))
    expect(screen.getByText(/\+S\$1,000\.00/)).toBeTruthy()
  })

  it('a TRANSFER still shows no sign — two legs, one sign would be meaningless', () => {
    show(
      opening({
        kind: 'transfer',
        description: 'Move',
        fromAccountId: 'a1',
        fromAmount: 1000,
        fromCurrency: 'SGD',
        toAccountId: 'a1',
        toAmount: 1000,
        toCurrency: 'SGD',
      }),
    )
    expect(screen.queryByText(/[+−]S\$1,000\.00/)).toBeNull()
  })

  it('disables Delete for the account Starting Balance', () => {
    show(opening({ source: 'opening' }))

    const deleteButton = screen.getByRole('button', { name: 'Delete' }) as HTMLButtonElement
    expect(deleteButton.disabled).toBe(true)
  })
})
