// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

afterEach(cleanup)

const account: Account = {
  id: 'acc_123',
  name: 'Lighthouse Bank',
  kind: 'asset',
  type: 'cash',
  institution: 'Lighthouse Bank',
  currency: 'VND',
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
}

const txn: Txn = {
  id: 'txn_efd1f8940000',
  date: '2026-06-29',
  kind: 'expense',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'Grab',
  memo: '',
  tags: [],
  splits: [],
  fromAccountId: account.id,
  fromAmount: 50_000,
  fromCurrency: 'VND',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: null,
  origCurrency: null,
  baseAmount: 2.46,
  baseCurrency: 'SGD',
  cleared: true,
  source: 'agent',
  createdAt: '2026-06-29T02:05:00Z',
  deleted: false,
  needsReview: false,
  externalRefs: [],
}

function renderDrawer() {
  return render(
    <TxnDetail
      txn={txn}
      accounts={[account]}
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
          source: 'agent',
          addedBy: 'Transaction & Trade Logger',
          createdAt: txn.createdAt,
          updatedAt: txn.createdAt,
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

describe('TxnDetail — transaction ID provenance', () => {
  it('shows the full ID and copies it with feedback', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    renderDrawer()

    const idButton = screen.getByRole('button', { name: `Copy transaction ID ${txn.id}` })
    expect(idButton.textContent).toBe(txn.id)

    fireEvent.click(idButton)

    expect(writeText).toHaveBeenCalledWith(txn.id)
    await waitFor(() => expect(idButton.textContent).toBe('Copied'))
  })
})
