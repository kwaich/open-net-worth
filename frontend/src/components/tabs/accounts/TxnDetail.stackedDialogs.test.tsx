// @vitest-environment jsdom
//
// A dialog the HOST opens for the drawer — TransactionSurfaces' delete confirm and add-category
// modal, rendered BESIDE the drawer rather than inside it — stacks on the drawer; it never
// closes it. The drawer is a non-modal Radix dialog, and Radix's default reads the focus such a
// dialog takes as it opens as "the user left": the drawer closed under the delete confirm, so
// Cancel had nothing to return to (owner report 2026-09-26). The add-category modal escaped only
// because focus was last inside the drawer (the category search it replaced); its case pins that
// the edit survives regardless. The harness mirrors TransactionSurfaces' shape: siblings.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import TxnDetail from './TxnDetail'
import { ConfirmModal } from './ConfirmModal'
import { AddCategoryModal } from './AddCategoryModal'
import type { Account, Category, Txn } from './types'

afterEach(cleanup)

const account: Account = {
  id: 'acc_123',
  name: 'Lighthouse Bank',
  kind: 'asset',
  type: 'cash',
  institution: 'Lighthouse Bank',
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
}

const txn: Txn = {
  id: 'txn_0a1b2c3d4e5f',
  date: '2026-09-03',
  kind: 'expense',
  category: null,
  payeeId: null,
  payeeName: null,
  description: 'Corner Grocer',
  memo: '',
  tags: [],
  splits: [],
  fromAccountId: account.id,
  fromAmount: 63.8,
  fromCurrency: 'SGD',
  toAccountId: null,
  toAmount: null,
  toCurrency: null,
  origAmount: null,
  origCurrency: null,
  baseAmount: 63.8,
  baseCurrency: 'SGD',
  cleared: false,
  source: 'manual',
  createdAt: '2026-09-02T15:22:00Z',
  deleted: false,
  needsReview: false,
  externalRefs: [],
}

function Host({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true)
  const [confirming, setConfirming] = useState(false)
  const [categories, setCategories] = useState<Category[]>([])
  const [catModal, setCatModal] = useState<{
    prefill: string
    onDone: (id: string) => void
  } | null>(null)
  return (
    <>
      {open && (
        <TxnDetail
          txn={txn}
          accounts={[account]}
          categories={categories}
          payeeOptions={[]}
          baseCurrency="SGD"
          rates={{}}
          onClose={() => {
            onClose()
            setOpen(false)
          }}
          onSave={() => {}}
          onDelete={() => setConfirming(true)}
          onMarkReviewed={() => {}}
          onManagePayees={() => {}}
          onCreateCategory={(prefill, _kind, cb) =>
            setCatModal({ prefill: prefill || '', onDone: cb })
          }
          onLoadActivity={() =>
            Promise.resolve({
              source: 'manual',
              addedBy: 'You',
              createdAt: txn.createdAt,
              updatedAt: txn.createdAt,
              activity: [],
            })
          }
          onLoadSourceEntries={() => Promise.resolve(null)}
          onDetachSourceRef={() => Promise.resolve()}
        />
      )}
      {confirming && (
        <ConfirmModal
          open
          title="Delete transaction"
          body="“Corner Grocer” will be removed."
          confirmLabel="Delete"
          danger
          onConfirm={() => setConfirming(false)}
          onClose={() => setConfirming(false)}
        />
      )}
      {catModal && (
        <AddCategoryModal
          open
          prefill={catModal.prefill}
          kind="expense"
          onClose={() => setCatModal(null)}
          onSave={({ name, kind }) => {
            const done = catModal.onDone
            setCatModal(null)
            setCategories((cs) => [...cs, { id: 'cat_petcare0001', label: name, kind } as Category])
            done('cat_petcare0001')
          }}
        />
      )}
    </>
  )
}

const drawer = () => document.querySelector('.acct-detail-wrap')
/** The drawer's heading: 'Edit transaction' only while the edit form is up. */
const kicker = () => document.querySelector('.acct-detail-kicker')?.textContent

describe('TxnDetail — a dialog the host opens for it stacks on it', () => {
  it('the delete confirm leaves the drawer open, and Cancel returns to it', async () => {
    const onClose = vi.fn()
    render(<Host onClose={onClose} />)

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete transaction' })
    expect(drawer()).not.toBeNull()

    fireEvent.click(within(confirm).getByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Delete transaction' })).toBeNull(),
    )
    expect(drawer()).not.toBeNull()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('focus moving to the page behind still closes it — only a dialog stacked on it is exempt', async () => {
    const onClose = vi.fn()
    render(
      <>
        <button type="button">Page behind</button>
        <Host onClose={onClose} />
      </>,
    )
    screen.getByRole('button', { name: 'Page behind' }).focus()
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(drawer()).toBeNull()
  })

  it('“+ New category” keeps the edit it was made for, and lands the new category in it', async () => {
    const onClose = vi.fn()
    render(<Host onClose={onClose} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit transaction' }))

    fireEvent.click(screen.getByRole('button', { name: /Category…/ }))
    fireEvent.change(screen.getByPlaceholderText('Search categories…'), {
      target: { value: 'Pet care' },
    })
    fireEvent.mouseDown(screen.getByRole('button', { name: /New category/ }))
    const modal = await screen.findByRole('dialog', { name: 'Add category' })
    expect(kicker()).toBe('Edit transaction')

    fireEvent.click(within(modal).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Add category' })).toBeNull())
    expect(kicker()).toBe('Edit transaction')
    expect(screen.getByRole('button', { name: /Pet care/ })).toBeTruthy()
    expect(onClose).not.toHaveBeenCalled()
  })
})
