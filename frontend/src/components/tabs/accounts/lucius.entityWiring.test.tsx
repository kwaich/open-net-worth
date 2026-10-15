// @vitest-environment jsdom
//
// Regression: the reimplemented Accounts tab (the former Cash tab) dropped ALL
// Lucius mention wiring — account rows, register transaction rows, and the
// net-worth hero stopped emitting data-entity-id / data-ctx, so they could no
// longer be @-mentioned, dragged into the dock, or right-clicked "Ask Lucius".
// These tests lock the wiring back in at the leaf-component level.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import { Sidebar } from './Sidebar'
import { TxnRow } from './TxnRow'
import TxnDetail from './TxnDetail'
import type { Account, AccountGroup, Txn } from './types'

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
    balance: 12500,
    balanceBase: 12500,
    clearedBalance: 12500,
    clearedBalanceBase: 12500,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-20',
    kind: 'expense',
    category: 'dining',
    payeeId: 'p1',
    payeeName: 'Kestrel Eats',
    description: 'Kestrel Eats order',
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
    createdAt: '2026-06-20',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderRow(t: Txn) {
  return render(
    <TxnRow
      t={t}
      accounts={[makeAccount()]}
      showAccount={false}
      selectedId={'a1'}
      selected={false}
      onToggle={() => {}}
      onUpdate={() => {}}
      splitOpen={false}
      onSplit={() => {}}
      payeeOptions={[]}
      payeeName={'Kestrel Eats'}
      tmpl={'1fr'}
      colKeys={[
        'cb',
        'clr',
        'date',
        'payee',
        'category',
        'description',
        'memo',
        'source',
        'amount',
      ]}
      rates={{}}
      categories={[]}
    />,
  )
}

describe('Accounts tab — Lucius account wiring (Sidebar)', () => {
  function renderSidebar(accounts: Account[]) {
    const groups: AccountGroup[] = [
      { key: 'cash', label: 'Cash', color: '#3b82f6', accounts, totalBase: 12500 },
    ]
    return render(
      <Sidebar
        accounts={accounts}
        groups={groups}
        totals={{
          cash: 12500,
          fixed_deposit: 0,
          investment: 0,
          retirement: 0,
          receivable: 0,
          credit: 0,
          loan: 0,
          liabilities: 0,
        }}
        selectedId={null}
        netWorth={12500}
        collapsed={false}
        onToggleCollapse={() => {}}
        onPointerDown={() => {}}
        onSelect={() => {}}
        onAdd={() => {}}
        onManagePayees={() => {}}
      />,
    )
  }

  it('emits each account row as a draggable account mention entity', () => {
    const { container } = renderSidebar([makeAccount()])
    const row = container.querySelector<HTMLElement>('[data-entity-id="account:a1"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('draggable')).toBe('true')
    expect(row!.getAttribute('data-ctx-type')).toBe('account')
    expect(row!.getAttribute('data-entity-label')).toBe('Meridian Savings')
    // Account color tints the chip glyph.
    expect(row!.getAttribute('data-entity-color')).toBe('#3b82f6')

    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.name).toBe('Meridian Savings')
    expect(typeof fields.balance).toBe('string') // formatted, never a bare number
  })

  it('emits the net-worth hero as the singleton net_worth entity (empty ref)', () => {
    const { container } = renderSidebar([makeAccount()])
    const nw = container.querySelector<HTMLElement>('[data-entity-id="net_worth:"]')
    expect(nw).toBeTruthy()
    expect(nw!.getAttribute('data-ctx-type')).toBe('net_worth')
    const fields = JSON.parse(nw!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.net_worth).toBeTruthy()
  })

  it('marks the account list as the nav screen-context region', () => {
    const { container } = renderSidebar([makeAccount()])
    const nav = container.querySelector<HTMLElement>('[data-ctx-region="nav"]')
    expect(nav).toBeTruthy()
    expect(nav!.getAttribute('data-ctx-region-label')).toBe('Accounts')
  })

  // Regression: ctx-fields harvest used to call fBase()/fCur() without
  // { cents: true }, so Lucius's screen context silently rounded amounts to
  // whole currency units while the on-screen figure kept its cents.
  it('keeps cents precision in a securities account balance ctx field', () => {
    const account = makeAccount({ id: 'a2', role: 'securities', holdingsBase: 100.63 })
    const { container } = renderSidebar([account])
    const row = container.querySelector<HTMLElement>('[data-entity-id="account:a2"]')
    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.balance).toMatch(/\.63/)
  })

  it('keeps cents precision in the net-worth hero ctx fields', () => {
    const accounts = [makeAccount()]
    const groups: AccountGroup[] = [
      { key: 'cash', label: 'Cash', color: '#3b82f6', accounts, totalBase: 12500.37 },
    ]
    const { container } = render(
      <Sidebar
        accounts={accounts}
        groups={groups}
        totals={{
          cash: 12500.37,
          fixed_deposit: 0,
          investment: 0,
          retirement: 0,
          receivable: 0,
          credit: 0,
          loan: 0,
          liabilities: 0,
        }}
        selectedId={null}
        netWorth={12500.37}
        collapsed={false}
        onToggleCollapse={() => {}}
        onPointerDown={() => {}}
        onSelect={() => {}}
        onAdd={() => {}}
        onManagePayees={() => {}}
      />,
    )
    const nw = container.querySelector<HTMLElement>('[data-entity-id="net_worth:"]')
    const fields = JSON.parse(nw!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.net_worth).toMatch(/\.37/)
    expect(fields.assets).toMatch(/\.37/)
  })
})

describe('Accounts tab — Lucius transaction wiring (TxnRow)', () => {
  it('emits each register row as a draggable transaction mention entity', () => {
    const { container } = renderRow(makeTxn())
    const row = container.querySelector<HTMLElement>('[data-entity-id="transaction:t1"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('draggable')).toBe('true')
    expect(row!.getAttribute('data-ctx-type')).toBe('transaction')
    // Chip label = the recognizable payee, not the raw bank description.
    expect(row!.getAttribute('data-entity-label')).toBe('Kestrel Eats')
    // The picker sublabel carries payee · account · date · signed amount immediately from the DOM.
    expect(row!.getAttribute('data-entity-sublabel')).toMatch(/Kestrel Eats/)

    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.payee).toBe('Kestrel Eats')
    expect(fields.category).toBe('dining')
    expect(typeof fields.amount_base).toBe('string') // formatted base figure
    expect(fields.status).toBe('Cleared')
  })

  it('routes a transfer to from_account / to_account instead of a single account field', () => {
    const { container } = renderRow(
      makeTxn({
        id: 't2',
        kind: 'transfer',
        category: null,
        payeeId: null,
        description: 'Move to savings',
        toAccountId: 'a1',
        toAmount: 100,
        toCurrency: 'SGD',
      }),
    )
    const row = container.querySelector<HTMLElement>('[data-entity-id="transaction:t2"]')
    expect(row).toBeTruthy()
    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    // Transfers never carry a category, and use from/to rather than the single account key.
    expect(fields.category).toBeUndefined()
    expect('account' in fields).toBe(false)
  })

  // Regression: amount_base used to be formatted without { cents: true }, so a
  // -100.63 transaction reported to Lucius as "-101" instead of "-100.63".
  it('keeps cents precision in the amount_base ctx field', () => {
    const { container } = renderRow(makeTxn({ id: 't3', fromAmount: 100.63, baseAmount: 100.63 }))
    const row = container.querySelector<HTMLElement>('[data-entity-id="transaction:t3"]')
    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.amount_base).toMatch(/\.63/)
  })
})

describe('Accounts tab — Description cell hover tooltip (NOTE1)', () => {
  it('sets the full description as the Description cell title attribute, matching the amount cell pattern', () => {
    const longDescription =
      'Reimbursement for the team offsite dinner at the place downtown near the office'
    const { container } = renderRow(makeTxn({ id: 't5', description: longDescription }))
    const memoCell = container.querySelector<HTMLElement>('.acct-td-memo')
    expect(memoCell).toBeTruthy()
    expect(memoCell!.getAttribute('title')).toBe(longDescription)
  })

  it('does not add an empty title attribute when the description is blank', () => {
    const { container } = renderRow(makeTxn({ id: 't6', description: '' }))
    const memoCell = container.querySelector<HTMLElement>('.acct-td-memo')
    expect(memoCell).toBeTruthy()
    expect(memoCell!.hasAttribute('title')).toBe(false)
  })
})

describe('Accounts tab — Lucius transaction wiring (TxnDetail)', () => {
  it("keeps cents precision in the drawer's amount_base ctx field", () => {
    const t = makeTxn({ id: 't4', fromAmount: 100.63, baseAmount: 100.63 })
    render(
      <TxnDetail
        txn={t}
        accounts={[makeAccount()]}
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
            createdAt: '2026-06-20',
            updatedAt: '2026-06-20',
            activity: [],
          })
        }
        onLoadSourceEntries={() => Promise.resolve(null)}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    const row = document.body.querySelector<HTMLElement>('[data-entity-id="transaction:t4"]')
    const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
    expect(fields.amount_base).toMatch(/\.63/)
  })
})
