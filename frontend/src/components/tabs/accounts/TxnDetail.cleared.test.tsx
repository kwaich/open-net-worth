// @vitest-environment jsdom
// The drawer marks a transaction cleared/uncleared in BOTH modes — View flips it at once
// (like the register's cleared column), Edit saves it with the rest of the form. The
// Starting Balance is always cleared, so its switch is disabled.
import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import TxnDetail from './TxnDetail'
import { detailPatchToUpdate } from './data'
import type { Account, Txn } from './types'

afterEach(cleanup)

const ACCT = {
  id: 'a1',
  name: 'Harborview Checking',
  kind: 'asset',
  type: 'cash',
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
} as unknown as Account

function makeTxn(over: Partial<Txn> = {}): Txn {
  return {
    id: 't1',
    date: '2026-06-15',
    kind: 'expense',
    category: null,
    payeeId: null,
    payeeName: null,
    description: 'Corner Grocer',
    memo: '',
    tags: [],
    splits: [],
    fromAccountId: 'a1',
    fromAmount: 42,
    fromCurrency: 'USD',
    toAccountId: null,
    toAmount: null,
    toCurrency: null,
    origAmount: null,
    origCurrency: null,
    baseAmount: 42,
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

function renderDrawer(
  txn: Txn,
  opts: {
    mode?: 'view' | 'edit'
    onToggleCleared?: (t: Txn, cleared: boolean) => void
    onSave?: (id: string, patch: Record<string, unknown>) => void
  } = {},
) {
  render(
    <TxnDetail
      txn={txn}
      initialMode={opts.mode ?? 'view'}
      accounts={[ACCT]}
      categories={[]}
      payeeOptions={[]}
      baseCurrency="USD"
      rates={{}}
      onClose={() => {}}
      onSave={opts.onSave ?? (() => {})}
      onDelete={() => {}}
      onMarkReviewed={() => {}}
      onToggleCleared={opts.onToggleCleared}
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
  return screen.getByRole('checkbox', { name: /cleared/i }) as HTMLInputElement
}

describe('TxnDetail — cleared status', () => {
  it('View shows the status and flips it at once', () => {
    const onToggleCleared = vi.fn()
    const sw = renderDrawer(makeTxn({ cleared: true }), { onToggleCleared })
    expect(sw.checked).toBe(true)
    fireEvent.click(sw)
    expect(onToggleCleared).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }), false)
  })

  it('View marks an uncleared transaction cleared', () => {
    const onToggleCleared = vi.fn()
    const sw = renderDrawer(makeTxn({ cleared: false }), { onToggleCleared })
    expect(sw.checked).toBe(false)
    fireEvent.click(sw)
    expect(onToggleCleared).toHaveBeenCalledWith(expect.objectContaining({ id: 't1' }), true)
  })

  it('beside the box an uncleared transaction reads "Pending", a cleared one "Posted"', () => {
    renderDrawer(makeTxn({ cleared: false }), { onToggleCleared: vi.fn() })
    expect(screen.getByText('Pending')).toBeTruthy()
    expect(screen.queryByText('Posted')).toBeNull()
    cleanup()
    renderDrawer(makeTxn({ cleared: true }), { onToggleCleared: vi.fn() })
    expect(screen.getByText('Posted')).toBeTruthy()
    expect(screen.queryByText('Pending')).toBeNull()
  })

  it('the Starting Balance switch is disabled', () => {
    const sw = renderDrawer(makeTxn({ source: 'opening' }), { onToggleCleared: vi.fn() })
    expect(sw.disabled).toBe(true)
  })

  it('Edit saves a changed status with the form', () => {
    let patch: Record<string, unknown> | null = null
    const sw = renderDrawer(makeTxn({ cleared: true }), {
      mode: 'edit',
      onSave: (_id, p) => {
        patch = p
      },
    })
    fireEvent.click(sw)
    fireEvent.click(screen.getByText('Save changes'))
    expect(patch).toMatchObject({ cleared: false })
  })

  it('Edit leaves an untouched status out of the patch', () => {
    let patch: Record<string, unknown> | null = null
    renderDrawer(makeTxn({ cleared: true }), {
      mode: 'edit',
      onSave: (_id, p) => {
        patch = p
      },
    })
    fireEvent.click(screen.getByText('Save changes'))
    expect(patch).not.toHaveProperty('cleared')
  })

  it('the drawer patch maps cleared onto the update', () => {
    expect(detailPatchToUpdate({ cleared: false }).cleared).toBe(false)
    expect(detailPatchToUpdate({}).cleared).toBeUndefined()
  })
})
