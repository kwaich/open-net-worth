// @vitest-environment jsdom
//
// Regression: dragging the drawer's left-edge resize handle and releasing the mouse over
// the backdrop (common when widening the drawer — the pointer ends up left of the handle,
// over `.acct-detail-wrap`) made the drawer disappear right after release. A real browser
// synthesizes a `click` on the nearest common ancestor of the mousedown/mouseup targets —
// here, the backdrop itself — even though the resize handle's mousedown calls
// stopPropagation(). Since the backdrop's onClick closes the drawer in view mode, that
// synthetic click was read as "click outside" and closed it. jsdom does not auto-synthesize
// that click, so this test fires it explicitly to reproduce what a real browser does.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import type { Account, Category, Txn } from './types'

afterEach(cleanup)

function makeAccount(over: Partial<Account> = {}): Account {
  return {
    id: 'a1',
    name: 'Northgate Rewards Card',
    kind: 'asset',
    type: 'credit',
    institution: 'Northgate',
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
    balance: -100,
    balanceBase: -100,
    clearedBalance: -100,
    clearedBalanceBase: -100,
    role: 'cash',
    holdingsBase: 0,
    ...over,
  }
}

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-17',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'Test',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 28.18,
    fromCurrency: 'SGD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 28.18,
    baseCurrency: 'SGD',
    cleared: true,
    source: 'manual',
    createdAt: '2026-06-17',
    deleted: false,
    needsReview: false,
    externalRefs: [],
    ...over,
  }
}

const activity = Promise.resolve({
  source: 'manual',
  addedBy: 'You',
  createdAt: '2026-06-17',
  updatedAt: '2026-06-17',
  activity: [],
})

function renderDrawer(onClose: () => void) {
  return render(
    <TxnDetail
      txn={makeTxn()}
      accounts={[makeAccount()]}
      categories={[] as Category[]}
      payeeOptions={[]}
      baseCurrency="SGD"
      rates={{}}
      onClose={onClose}
      onSave={() => {}}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onManagePayees={() => {}}
      onCreateCategory={() => {}}
      onLoadActivity={() => activity}
      onLoadSourceEntries={() => Promise.resolve(null)}
      onDetachSourceRef={() => Promise.resolve()}
    />,
  )
}

describe('TxnDetail — resize-drag does not close the drawer', () => {
  it('a drag-release click landing on the backdrop is swallowed, not read as click-outside', async () => {
    const onClose = vi.fn()
    renderDrawer(onClose)
    const handle = document.querySelector('.acct-detail-resize') as HTMLElement
    const backdrop = document.querySelector('.acct-detail-wrap') as HTMLElement
    expect(handle).toBeTruthy()
    expect(backdrop).toBeTruthy()

    fireEvent.pointerDown(handle, {
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
      clientX: 500,
    })
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 560 }) // drag left, widening the drawer
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 560 })
    // What the real browser does next: synthesizes `click` on the common ancestor of the
    // mousedown/mouseup targets — the backdrop — since the pointer ended up over it.
    fireEvent.click(backdrop)

    expect(onClose).not.toHaveBeenCalled()
    expect(screen.getByRole('dialog', { name: 'Transaction details' })).toBeTruthy()
  })

  it('a genuine later click on the backdrop (unrelated to any drag) still closes it', async () => {
    const onClose = vi.fn()
    renderDrawer(onClose)
    const backdrop = document.querySelector('.acct-detail-wrap') as HTMLElement

    fireEvent.click(backdrop)

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('the swallow is a one-shot: a second, later backdrop click after a drag still closes', async () => {
    const onClose = vi.fn()
    renderDrawer(onClose)
    const handle = document.querySelector('.acct-detail-resize') as HTMLElement
    const backdrop = document.querySelector('.acct-detail-wrap') as HTMLElement

    fireEvent.pointerDown(handle, {
      pointerId: 1,
      pointerType: 'mouse',
      isPrimary: true,
      clientX: 500,
    })
    fireEvent.pointerMove(handle, { pointerId: 1, clientX: 560 })
    fireEvent.pointerUp(handle, { pointerId: 1, clientX: 560 })
    fireEvent.click(backdrop)
    expect(onClose).not.toHaveBeenCalled()

    // Let the one-tick suppression flag reset (queued via setTimeout(0) after mouseup).
    await new Promise((r) => setTimeout(r, 10))

    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
