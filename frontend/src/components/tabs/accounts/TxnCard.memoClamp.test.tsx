// @vitest-environment jsdom

import { cleanup, render } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import './accounts.css'
import { TxnCard } from './TxnCard'
import type { Account, Txn } from './types'

afterEach(cleanup)

const account: Account = {
  id: 'a1',
  name: 'Meridian Savings',
  kind: 'asset',
  type: 'cash',
  institution: 'Meridian',
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
  balance: 100,
  balanceBase: 100,
  clearedBalance: 100,
  clearedBalanceBase: 100,
  role: 'cash',
  holdingsBase: 0,
}

const txn: Txn = {
  id: 't1',
  date: '2026-07-20',
  kind: 'expense',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'FAIRPRICE #123',
  memo: 'A deliberately long personal memo that needs several lines on a narrow phone screen.',
  tags: [],
  splits: [],
  fromAccountId: 'a1',
  fromAmount: 42.5,
  fromCurrency: 'SGD',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: null,
  origCurrency: null,
  baseAmount: 42.5,
  baseCurrency: 'SGD',
  cleared: true,
  source: 'manual',
  createdAt: '2026-07-20',
  deleted: false,
  needsReview: false,
  externalRefs: [],
}

describe('TxnCard memo', () => {
  it('clamps long mobile memos to three lines with overflow hidden for an ellipsis', () => {
    const { container } = render(
      <TxnCard
        t={txn}
        accounts={[account]}
        showAccount={false}
        selectedId="a1"
        selected={false}
        onToggle={() => {}}
        onUpdate={() => {}}
        payeeName={null}
        rates={{}}
        splitOpen={false}
        onSplit={() => {}}
      />,
    )

    const memo = container.querySelector<HTMLElement>('.acct-card-memo')
    expect(memo).not.toBeNull()
    const style = getComputedStyle(memo!)
    expect(style.display).toBe('-webkit-box')
    expect(style.getPropertyValue('-webkit-line-clamp')).toBe('3')
    expect(style.overflow).toBe('hidden')
  })
})
