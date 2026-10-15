// @vitest-environment jsdom
//
// Regression (owner report 2026-09-20): an income stored on the FROM leg — written before the
// ledger refused that shape — opened in Edit as amount 0 with no account, and its save was
// refused for exactly that; the refusal's toast sat under the phone sheet, so Save read as a
// tap that did nothing. The form now seeds from whichever leg has the money, saves it back on
// the income's own leg, and shows a refused save beside the button.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

afterEach(cleanup)

const CASH: Account = {
  id: 'cash-1',
  name: 'Everyday Checking',
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
} as Account

const INCOME_ON_FROM_LEG: Txn = {
  id: 'i1',
  date: '2026-09-12',
  kind: 'income',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'PAYNOW RECEIVED FROM A FRIEND',
  memo: '',
  tags: [],
  splits: [],
  fromAccountId: 'cash-1',
  fromAmount: 20,
  fromCurrency: 'SGD',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: null,
  origCurrency: null,
  baseAmount: 20,
  baseCurrency: 'SGD',
  cleared: false,
  source: 'automation',
  createdAt: '2026-09-12',
  deleted: false,
  needsReview: true,
  externalRefs: [],
}

function renderEdit(onSave: (id: string, patch: Record<string, unknown>) => Promise<void> | void) {
  return render(
    <TxnDetail
      txn={INCOME_ON_FROM_LEG}
      initialMode="edit"
      accounts={[CASH]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={{}}
      onClose={() => {}}
      onSave={onSave}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() =>
        Promise.resolve({
          source: 'automation',
          addedBy: 'Automation',
          createdAt: '2026-09-12',
          updatedAt: '2026-09-12',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

describe('TxnDetail — an income stored on the from-leg', () => {
  it('opens with its amount and account, and saves them back on the income leg', () => {
    let saved: Record<string, unknown> | null = null
    renderEdit((_id, patch) => {
      saved = patch
    })
    const amount = document.querySelector('.acct-edit-amt-in') as HTMLInputElement
    expect(amount.value).toBe('20')
    fireEvent.click(screen.getByText('Save changes'))
    expect(saved).not.toBeNull()
    const patch = saved as unknown as Record<string, unknown>
    expect(patch.amount).toBe(20)
    expect(patch.toAccountId).toBe('cash-1')
    expect(patch.fromAccountId).toBeNull()
  })

  it('shows a refused save beside the button instead of doing nothing', async () => {
    renderEdit(() => Promise.reject(new Error('income requires a destination leg')))
    fireEvent.click(screen.getByText('Save changes'))
    expect((await screen.findByRole('alert')).textContent).toContain(
      'income requires a destination leg',
    )
    // Still in Edit, with the draft intact.
    expect(screen.getByText('Save changes')).toBeTruthy()
  })
})
