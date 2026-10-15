// @vitest-environment jsdom
//
// Regression: EditBody's save() unconditionally wrote `patch.fromAccountId = f.account;
// patch.toAccountId = null` for ANY kind==='investment' save — hardcoding "buy" (cash OUT,
// from-leg) regardless of the transaction's actual original direction. An investment SELL
// (cash IN, to-leg only — no fromAccountId) opened for editing and saved with ZERO changes
// (e.g. just to fix a typo in the note) would silently flip into a buy-shaped record: the
// account that received sale proceeds would instead look like the account money left. That
// corrupts cash-flow direction for net worth / cash-as-of-month (services/planning.py's
// _plan_leg_ok reads from_account_id vs to_account_id to decide which side of the ledger
// moved). EditBody has no buy/sell toggle UI at all (unlike adjustment's increase/decrease
// sign toggle) — direction must simply be PRESERVED from the original transaction, never
// flipped, since there is no user-facing way to intentionally change it here.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Harborview',
    kind: 'asset',
    type: 'investment',
    institution: '',
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
    balance: 0,
    balanceBase: 0,
    clearedBalance: 0,
    clearedBalanceBase: 0,
    role: 'securities',
    holdingsBase: 0,
    ...over,
  }
}

const ACCT = makeAccount()

function makeInvestmentSell(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-15',
    kind: 'investment',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'ONWAI sale proceeds',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: null,
    fromAmount: null,
    fromCurrency: null,
    toAccountId: 'a1',
    toAmount: 8000.0,
    toCurrency: 'USD',
    origAmount: null,
    origCurrency: null,
    baseAmount: 8000.0,
    baseCurrency: 'USD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-15',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function makeInvestmentBuy(over: Partial<Txn> = {}): Txn {
  return {
    id: 't2',
    date: '2026-06-15',
    kind: 'investment',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'ONWAI purchase',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 5000,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 5000,
    baseCurrency: 'USD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-15',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

function renderAndSave(t: Txn): Record<string, unknown> {
  let savedPatch: Record<string, unknown> | null = null
  render(
    <TxnDetail
      txn={t}
      accounts={[ACCT]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="USD"
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
          createdAt: '2026-06-15',
          updatedAt: '2026-06-15',
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
  return savedPatch as unknown as Record<string, unknown>
}

describe('TxnDetail — saving an investment transaction preserves its original buy/sell direction', () => {
  it('a SELL (to-leg only) saved with zero changes stays a sell — never gains a fromAccountId', () => {
    const patch = renderAndSave(makeInvestmentSell())
    expect(patch.toAccountId).toBe('a1')
    expect(patch.fromAccountId).toBeFalsy()
  })

  it('a BUY (from-leg only) saved with zero changes stays a buy — never gains a toAccountId', () => {
    const patch = renderAndSave(makeInvestmentBuy())
    expect(patch.fromAccountId).toBe('a1')
    expect(patch.toAccountId).toBeFalsy()
  })
})

// Regression: the edit-mode Amount sign badge hardcoded `f.kind === 'income' ? '+' : '−'`, so an
// investment SELL (a to-leg-only cash inflow, e.g. "+$2,000.00" sale proceeds) rendered with a
// misleading "−" badge in Edit even though the View header correctly showed "+". The stored amount
// and the save were always correct — the badge just disagreed. It must mirror the View's inflow
// rule: income OR an investment sell reads positive.
function renderEdit(t: Txn) {
  render(
    <TxnDetail
      txn={t}
      accounts={[ACCT]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="USD"
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
          createdAt: '2026-06-15',
          updatedAt: '2026-06-15',
          activity: [],
        })
      }
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
  fireEvent.click(screen.getByText('Edit transaction'))
  return document.body.querySelector('.acct-edit-sign') as HTMLElement
}

describe('TxnDetail — edit-mode Amount sign badge reflects cash direction', () => {
  it('shows "+" (pos) for an investment SELL, not "−"', () => {
    const sign = renderEdit(makeInvestmentSell())
    expect(sign.textContent).toBe('+')
    expect(sign.className).toContain('pos')
    expect(sign.className).not.toContain('neg')
  })

  it('shows "−" (neg) for an investment BUY', () => {
    const sign = renderEdit(makeInvestmentBuy())
    expect(sign.textContent).toBe('−')
    expect(sign.className).toContain('neg')
  })
})

// The Invest sign is a buy ⇄ sell toggle, like Adjustment's raise/lower — a buy booked by
// mistake (or a sale drafted as a buy) is flipped here, not deleted and re-entered.
describe('TxnDetail — the Invest sign flips buy ⇄ sell', () => {
  function flipAndSave(t: Txn) {
    let savedPatch: Record<string, unknown> | null = null
    render(
      <TxnDetail
        txn={t}
        initialMode="edit"
        accounts={[ACCT]}
        categories={[]}
        payeeOptions={[]}
        baseCurrency="USD"
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
            createdAt: '2026-06-15',
            updatedAt: '2026-06-15',
            activity: [],
          })
        }
        onLoadSourceEntries={() => Promise.resolve(null)}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    const sign = screen.getByRole('button', { name: /switch buy \/ sell/i })
    fireEvent.click(sign)
    fireEvent.click(screen.getByText('Save changes'))
    return { sign, patch: savedPatch as unknown as Record<string, unknown> }
  }

  it('a BUY flipped saves as a sell (to-leg only) and reads "+"', () => {
    const { sign, patch } = flipAndSave(makeInvestmentBuy())
    expect(sign.textContent).toBe('+')
    expect(patch.toAccountId).toBe('a1')
    expect(patch.fromAccountId).toBeFalsy()
  })

  it('a SELL flipped saves as a buy (from-leg only) and reads "−"', () => {
    const { sign, patch } = flipAndSave(makeInvestmentSell())
    expect(sign.textContent).toBe('−')
    expect(patch.fromAccountId).toBe('a1')
    expect(patch.toAccountId).toBeFalsy()
  })
})

// Switching INTO Invest carries the sign the user was looking at: income (cash in) becomes a
// sell, expense (cash out) a buy — never a silent flip from "+" to "−".
describe('TxnDetail — switching to Invest keeps the cash direction', () => {
  function switchToInvestAndSave(t: Txn) {
    let savedPatch: Record<string, unknown> | null = null
    render(
      <TxnDetail
        txn={t}
        initialMode="edit"
        accounts={[ACCT]}
        categories={[]}
        payeeOptions={[]}
        baseCurrency="USD"
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
            createdAt: '2026-06-15',
            updatedAt: '2026-06-15',
            activity: [],
          })
        }
        onLoadSourceEntries={() => Promise.resolve(null)}
        onDetachSourceRef={() => Promise.resolve()}
      />,
    )
    fireEvent.click(screen.getByText('Invest'))
    const sign = document.body.querySelector('.acct-edit-sign') as HTMLElement
    fireEvent.click(screen.getByText('Save changes'))
    return { sign, patch: savedPatch as unknown as Record<string, unknown> }
  }

  it('Income → Invest stays "+" and saves as a sell', () => {
    const { sign, patch } = switchToInvestAndSave(makeInvestmentSell({ kind: 'income' }))
    expect(sign.textContent).toBe('+')
    expect(patch.toAccountId).toBe('a1')
    expect(patch.fromAccountId).toBeFalsy()
  })

  it('Expense → Invest stays "−" and saves as a buy', () => {
    const { sign, patch } = switchToInvestAndSave(makeInvestmentBuy({ kind: 'expense' }))
    expect(sign.textContent).toBe('−')
    expect(patch.fromAccountId).toBe('a1')
    expect(patch.toAccountId).toBeFalsy()
  })
})

// An investment row's payee is its broker (ADR 2026-08-25) — the edit form used to hide the
// Payee field for investment, so a trade booked without one could never be corrected.
describe('TxnDetail — an investment row can carry a payee', () => {
  it('shows the Payee field in edit mode for a buy and a sell', () => {
    renderEdit(makeInvestmentBuy())
    expect(screen.getByText('The broker or platform')).toBeTruthy()
    cleanup()
    renderEdit(makeInvestmentSell())
    expect(screen.getByText('The broker or platform')).toBeTruthy()
  })
})
