// @vitest-environment jsdom
//
// An investment row's amount reads PURPLE in the register — the same hue the drawer
// already used for `Type: Investment` (.acct-detail-kind.investment). Before this,
// a BOUGHT row was painted like any other outflow (neutral ink) / a SELL like income
// (green), so nothing in the register said "this moved securities, not spending".

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { TxnRow, type RowPatch } from './TxnRow'
import { TxnCard } from './TxnCard'
import type { Account, Txn } from './types'

afterEach(cleanup)

function makeAccount(): Account {
  return {
    id: 'a1',
    name: 'Harborview (USD)',
    kind: 'asset',
    type: 'investment',
    institution: 'Harborview',
    currency: 'USD',
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
    balance: 1000,
    balanceBase: 1000,
    clearedBalance: 1000,
    clearedBalanceBase: 1000,
    role: 'securities',
    holdingsBase: 0,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-07-30',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'BOUGHT 2 META @ 538',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 1076,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 1388.44,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-07-30',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderRow(t: Txn, onUpdate: (id: string, patch: RowPatch) => void = () => {}) {
  return render(
    <TxnRow
      t={t}
      accounts={[makeAccount()]}
      showAccount={false}
      selectedId={'a1'}
      selected={false}
      onToggle={() => {}}
      onUpdate={onUpdate}
      splitOpen={false}
      onSplit={() => {}}
      payeeOptions={[]}
      payeeName={'Harborview'}
      tmpl={'1fr'}
      colKeys={['cb', 'clr', 'date', 'payee', 'category', 'description', 'source', 'amount']}
      rates={{}}
      categories={[]}
    />,
  )
}

function renderCard(t: Txn) {
  return render(
    <TxnCard
      t={t}
      accounts={[makeAccount()]}
      showAccount={false}
      selectedId={'a1'}
      selected={false}
      onToggle={() => {}}
      onUpdate={() => {}}
      payeeName={'Harborview'}
      rates={{}}
      splitOpen={false}
      onSplit={() => {}}
    />,
  )
}

describe('register amount colour — kind=investment', () => {
  it('marks a buy (from-leg) amount cell as investment-tinted', () => {
    const { container } = renderRow(makeTxn({ kind: 'investment' }))
    const amt = container.querySelector<HTMLElement>('.acct-td-amt')
    expect(amt).toBeTruthy()
    expect(amt!.classList.contains('invest')).toBe(true)
  })

  it('marks a sell (to-leg) amount cell as investment-tinted too — the hue is the kind, not the sign', () => {
    const { container } = renderRow(
      makeTxn({
        kind: 'investment',
        description: 'SOLD 2 META @ 538',
        fromAccountId: null,
        fromAmount: null,
        fromCurrency: null,
        toAccountId: 'a1',
        toAmount: 1076,
        toCurrency: 'USD',
      }),
    )
    const amt = container.querySelector<HTMLElement>('.acct-td-amt')
    expect(amt!.classList.contains('invest')).toBe(true)
    expect(amt!.classList.contains('pos')).toBe(true)
  })

  it('leaves every other kind on its sign-based colour', () => {
    for (const kind of ['expense', 'income', 'transfer', 'adjustment'] as const) {
      const { container, unmount } = renderRow(makeTxn({ kind }))
      const amt = container.querySelector<HTMLElement>('.acct-td-amt')
      expect(amt!.classList.contains('invest'), kind).toBe(false)
      unmount()
    }
  })

  it('tints the mobile card amount the same way', () => {
    const { container } = renderCard(makeTxn({ kind: 'investment' }))
    const amt = container.querySelector<HTMLElement>('.acct-card-amt')
    expect(amt!.classList.contains('invest')).toBe(true)
  })

  // jsdom does not resolve var() in getComputedStyle (and vitest stubs CSS imports to ''), so
  // read the sheet and assert the declaration itself: the register tint must stay the same hue
  // as the drawer's Type: Investment label.
  it('uses the preserved violet token for every investment selector', () => {
    const css = readFileSync(
      resolve(process.cwd(), 'src/components/tabs/accounts/accounts.css'),
      'utf8',
    )
    const decl = (selector: string) =>
      css
        .match(new RegExp(selector.replace(/\./g, '\\.') + '\\s*\\{[^}]*color:\\s*([^;}]+)'))?.[1]
        ?.trim()
    for (const selector of [
      '.acct-td-amt.invest',
      '.acct-card-amt.invest',
      '.acct-detail-kind.investment',
      '.acct-edit-seg-btn.on.investment',
    ]) {
      expect(decl(selector), selector).toBe('var(--violet)')
    }
  })

  it('keeps historical FX-pair provenance on the preserved violet token', () => {
    const css = readFileSync(
      resolve(process.cwd(), 'src/components/tabs/accounts/accounts.css'),
      'utf8',
    )
    const block = css.match(/\.imp-flag\.fx_transfer_pair\s*\{([^}]+)\}/)?.[1] ?? ''
    expect(block).toContain('color: var(--violet)')
    expect(block).toContain('color-mix(in srgb, var(--violet) 12%, transparent)')
  })
})

// MATH-9: the inline amount editor used to pre-compute `baseAmount` on the patch, but nothing
// downstream (AccountsTab's updateFromPatch) ever read it — the server recomputes base amount
// on write. Guard against that dead field coming back.
describe('inline amount editor patch', () => {
  it('emits no baseAmount key — the server derives it, the client never should', () => {
    const patches: RowPatch[] = []
    const { container } = renderRow(makeTxn({ kind: 'expense' }), (_id, patch) => {
      patches.push(patch)
    })
    const cell = container.querySelector<HTMLElement>('.acct-td-amt')
    expect(cell).toBeTruthy()
    fireEvent.doubleClick(cell!)
    const input = container.querySelector<HTMLInputElement>('.acct-editin')
    expect(input).toBeTruthy()
    fireEvent.change(input!, { target: { value: '55' } })
    fireEvent.blur(input!)
    expect(patches).toHaveLength(1)
    expect(Object.keys(patches[0]!)).not.toContain('baseAmount')
  })
})
