// @vitest-environment jsdom

import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn, TxnActivity } from './types'

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

function makeTxn(source: string): Txn {
  return {
    id: 'txn_ee5d471e6056',
    date: '2026-09-19',
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
    source,
    createdAt: '2026-09-19T02:05:00Z',
    deleted: false,
    needsReview: false,
    externalRefs: [],
  }
}

function renderDrawer(txn: Txn, activity: TxnActivity) {
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
      onLoadActivity={() => Promise.resolve(activity)}
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

function addedByRow(): HTMLElement {
  const label = screen.getByText('Added by')
  return label.closest('.acct-detail-row') as HTMLElement
}

describe('TxnDetail — Added by badge', () => {
  it('drops the badge when it would only repeat the name', async () => {
    const txn = makeTxn('automation')
    renderDrawer(txn, {
      source: 'automation',
      addedBy: 'Automation',
      via: 'Log trade & transactions from emails',
      createdAt: txn.createdAt,
      updatedAt: txn.createdAt,
      activity: [
        {
          action: 'created',
          actor: 'automation',
          actorName: 'Automation',
          via: 'Log trade & transactions from emails',
          at: txn.createdAt,
        },
      ],
    })

    await screen.findAllByText(/via Log trade & transactions from emails/)
    const row = addedByRow()
    expect(row.textContent).toContain('Automation • via Log trade & transactions from emails')
    expect(row.querySelector('.acct-detail-badge')).toBeNull()

    const timelineRow = document.querySelector('.acct-detail-tl') as HTMLElement
    expect(timelineRow.textContent).toContain(
      'Automation • via Log trade & transactions from emails',
    )
    expect(timelineRow.querySelector('.acct-detail-badge')).toBeNull()
  })

  it('keeps the badge when the name says something the type does not', async () => {
    const txn = makeTxn('agent')
    renderDrawer(txn, {
      source: 'agent',
      addedBy: 'Transaction & Trade Logger',
      createdAt: txn.createdAt,
      updatedAt: txn.createdAt,
      activity: [],
    })

    await screen.findByText('Transaction & Trade Logger')
    const badge = addedByRow().querySelector('.acct-detail-badge')
    expect(badge?.textContent).toBe('Agent')
  })
})
