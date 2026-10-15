// @vitest-environment jsdom
//
// MATH-42 regression: the visible "≈ base" figure honors the locked base-currency
// stamp (ADR 2026-07-09) — `locked ? txn.baseAmount : toBase(...)` — but the Lucius
// screen-context field (`amount_base`, via EntityRow.toFields) recomputed a fresh,
// unconditional `toBase()` instead of reusing that same locked/shown value. A txn
// stamped at 135 (base SGD) with today's rate implying 137 showed "135.00" on
// screen while telling Lucius "137.00" — the two must always agree.

import { describe, it, expect, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Txn } from './types'

afterEach(cleanup)

const ACCT: Account = {
  id: 'a1',
  name: 'Meridian Checking',
  kind: 'asset',
  type: 'cash',
  institution: 'Meridian',
  currency: 'USD',
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
  balance: 0,
  balanceBase: 0,
  clearedBalance: 0,
  clearedBalanceBase: 0,
  role: 'cash',
  holdingsBase: 0,
}

// USD 100 at a live rate of 1.37 converts to SGD 137 — deliberately different from
// the locked stamp of 135 below, so a mismatch between the visible figure and the
// Lucius context field cannot hide behind equal numbers.
const RATES = { USD: 1.37, SGD: 1 }

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-20',
    kind: 'expense',
    category: 'dining',
    payeeId: 'p1',
    payeeName: null,
    description: 'Overseas order',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 100,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 135,
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

function show(t: Txn) {
  return render(
    <TxnDetail
      txn={t}
      accounts={[ACCT]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={RATES}
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
}

function ctxAmountBase(): string {
  const row = document.body.querySelector<HTMLElement>('[data-entity-id="transaction:t1"]')
  const fields = JSON.parse(row!.getAttribute('data-ctx-fields') ?? '{}')
  return fields.amount_base as string
}

function shownBaseText(): string {
  return document.body.querySelector('.acct-detail-base')?.textContent ?? ''
}

describe('TxnDetail — the visible ≈ base figure and the Lucius context field agree', () => {
  it('a LOCKED stamp (baseCurrency matches the current base): both show 135.00, not a live 137', () => {
    show(makeTxn())
    const shown = shownBaseText()
    expect(shown).toMatch(/135\.00/)
    expect(shown).not.toMatch(/137\.00/)
    const ctx = ctxAmountBase()
    expect(ctx).toMatch(/135\.00/)
    expect(ctx).not.toMatch(/137\.00/)
  })

  it('a STALE stamp (baseCurrency != current base): both fall back to the live 137.00', () => {
    show(makeTxn({ baseCurrency: 'EUR' }))
    const shown = shownBaseText()
    expect(shown).toMatch(/137\.00/)
    const ctx = ctxAmountBase()
    expect(ctx).toMatch(/137\.00/)
  })
})
