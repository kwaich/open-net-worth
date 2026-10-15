// @vitest-environment jsdom
//
// Mobile date-group headers (mission-control task 101, "group transactions by dates too when
// we show on mobile, like YNAB"). Covers: one header per distinct day, in the same order as the
// (default date-desc-sorted) cards; each card under a header drops its own per-row date since
// the header already carries it; a non-date sort falls back to the old ungrouped/per-row-date
// behavior, since scattering headers between non-contiguous same-day rows would read as noise.

import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Register } from './Register'
import type { Account, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
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
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-20',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'FAIRPRICE #123',
    memo: '',
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
    ...over,
  }
}

/** Forces useIsMobile() true (jsdom's default matchMedia stub reports desktop — see vitest.setup.ts).
 *  `globalThis.matchMedia` is installed as a getter-only accessor by vitest.setup.ts, so a plain
 *  `window.matchMedia = ...` assignment silently no-ops — must redefine the property instead. */
function mockMobile(isMobile: boolean) {
  const original = Object.getOwnPropertyDescriptor(window, 'matchMedia')
  const fn = ((query: string) => ({
    matches: isMobile,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
  return () => {
    if (original) Object.defineProperty(window, 'matchMedia', original)
  }
}

function renderRegister(txns: Txn[]) {
  return render(
    <Register
      txns={txns}
      total={txns.length}
      accounts={[makeAccount()]}
      showAccount={false}
      selectedId="a1"
      selectedIds={new Set()}
      onToggle={() => {}}
      onToggleAll={() => {}}
      onClearSelection={() => {}}
      onSelectRange={() => {}}
      onUpdate={() => {}}
      onAdd={() => {}}
      adding={false}
      onAdding={() => {}}
      categories={[]}
      allEmpty={false}
      loadingTxns={false}
      isLoadingMore={false}
      payeeOptions={[]}
      payeeNames={{}}
      rates={{}}
      hiddenCols={new Set()}
    />,
  )
}

describe('Register mobile date-group headers', () => {
  let restore: () => void
  beforeEach(() => {
    restore = mockMobile(true)
  })
  afterEach(() => restore())

  it('renders one header per distinct day, newest first, and drops the per-card date', () => {
    const txns = [
      makeTxn({ id: 't1', date: '2026-07-20' }),
      makeTxn({ id: 't2', date: '2026-07-20' }),
      makeTxn({ id: 't3', date: '2026-07-19' }),
    ]
    const { container } = renderRegister(txns)
    const headers = Array.from(container.querySelectorAll('.acct-card-dategroup')).map(
      (el) => el.textContent,
    )
    expect(headers).toEqual(['July 20, 2026', 'July 19, 2026'])
    // No per-card date left — the group header carries it instead.
    expect(container.querySelectorAll('.acct-card-date').length).toBe(0)
    expect(container.querySelectorAll('.acct-card').length).toBe(3)
  })

  it('a single day gets exactly one header', () => {
    const txns = [makeTxn({ id: 't1' }), makeTxn({ id: 't2' })]
    const { container } = renderRegister(txns)
    expect(container.querySelectorAll('.acct-card-dategroup').length).toBe(1)
    expect(container.querySelector('.acct-card-dategroup')?.textContent).toBe('July 20, 2026')
  })

  it('falls back to ungrouped per-card dates on the desktop grid (not mobile)', () => {
    restore()
    restore = mockMobile(false)
    const txns = [
      makeTxn({ id: 't1', date: '2026-07-20' }),
      makeTxn({ id: 't2', date: '2026-07-19' }),
    ]
    const { container } = renderRegister(txns)
    expect(container.querySelectorAll('.acct-card-dategroup').length).toBe(0)
  })
})
