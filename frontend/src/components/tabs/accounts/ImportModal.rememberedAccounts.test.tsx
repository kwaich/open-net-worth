// @vitest-environment jsdom
//
// Remembered account chips. A statement layout already remembers HOW to read it and the
// owner's typed instructions; it now also remembers WHICH ACCOUNTS it routes into, so a
// monthly export doesn't make the owner re-pick the same wallets every time.
//
// Read back through the same zero-cost `source-peek` probe that pre-fills instructions
// (`accountIds`, anchor first), and written back on preview as the full chip set.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

afterEach(cleanup)

const mkAcct = (id: string, name: string, currency: string) =>
  ({
    id,
    name,
    currency,
    color: '#3b86dd',
    kind: 'asset',
    type: 'cash',
    archived: false,
  }) as unknown as Account

const ACCOUNTS = [
  mkAcct('rvb', 'Riverside Bank', 'VND'),
  mkAcct('pocket', 'PocketPay Wallet', 'VND'),
  mkAcct('compass', 'Compass USD', 'USD'),
]

const CSV = 'Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n'

const preview = () => ({
  statementId: 'st-1',
  drafts: [
    {
      date: '2026-07-01',
      payee: null,
      description: 'COFFEE',
      amount: -12.5,
      currency: 'VND',
      kind: 'expense',
      category: null,
      dup: false,
      rowIndex: 0,
      external_refs: [],
      flags: [],
    },
  ],
  importable: 1,
  duplicates: 0,
  skipped: [],
  total: 1,
  anomaly: {
    near_duplicate: 0,
    transfer_pair: 0,
    intra_batch_duplicate: 0,
    amount_anomaly: 0,
    flagged_rows: 0,
  },
})

function open(props: Record<string, unknown> = {}) {
  const onPreview =
    (props.onPreview as ReturnType<typeof vi.fn>) || vi.fn().mockResolvedValue(preview())
  const r = render(
    <ImportModal
      open
      accounts={ACCOUNTS}
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
      {...(props as object)}
    />,
  )
  return { ...r, onPreview }
}

function pick(_container: HTMLElement) {
  const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], 'sao-ke.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  Object.defineProperty(file, 'size', { value: 41 * 1024 })
  fireEvent.change(input, { target: { files: [file] } })
}

const chipNames = (_container: HTMLElement) =>
  Array.from(document.body.querySelectorAll('.imp3-achip:not(.add)')).map((el) =>
    (el.textContent || '').replace('✕', '').trim(),
  )

const peekWith = (accountIds: string[]) =>
  vi.fn().mockResolvedValue({ known: true, instructions: '', formatLabel: null, accountIds })

describe('Choose step — accounts remembered with the layout', () => {
  it('restores the remembered chips when the source is recognised', async () => {
    const { container } = open({ onSourcePeek: peekWith(['pocket', 'rvb']) })
    pick(container)
    await waitFor(() =>
      expect(chipNames(container)).toEqual(['PocketPay Wallet', 'Riverside Bank']),
    )
    // Restoring accounts is what makes the CTA reachable without a manual pick.
    expect(
      (screen.getByRole('button', { name: /Read the statement/ }) as HTMLButtonElement).disabled,
    ).toBe(false)
  })

  it('keeps the account the import was opened from as the anchor, and adds the rest', async () => {
    // `defaultAccountId` is live context (the account page you clicked Import on) — a stronger
    // signal than last month's memory, so it stays first.
    const { container } = open({
      defaultAccountId: 'compass',
      onSourcePeek: peekWith(['pocket', 'rvb']),
    })
    pick(container)
    await waitFor(() =>
      expect(chipNames(container)).toEqual(['Compass USD', 'PocketPay Wallet', 'Riverside Bank']),
    )
  })

  it('drops a remembered account that no longer exists instead of rendering a dead chip', async () => {
    const { container } = open({ onSourcePeek: peekWith(['deleted-acct', 'pocket']) })
    pick(container)
    await waitFor(() => expect(chipNames(container)).toEqual(['PocketPay Wallet']))
  })

  it('an unknown source pre-selects nothing', async () => {
    const onSourcePeek = vi
      .fn()
      .mockResolvedValue({ known: false, instructions: '', accountIds: [] })
    const { container } = open({ onSourcePeek })
    pick(container)
    await waitFor(() => expect(onSourcePeek).toHaveBeenCalled())
    expect(chipNames(container)).toEqual([])
    expect(screen.getByRole('button', { name: 'Select account' })).toBeTruthy()
  })

  it('a restored anchor is still removable — its ✕ is not a no-op', async () => {
    // Regression: prefilling had to keep the anchor OUT of the extras list, because
    // removeAccount promotes extras[0] to anchor. An anchor that is also its own first extra
    // would re-elect itself and the chip could never be dismissed.
    const { container } = open({ onSourcePeek: peekWith(['pocket', 'rvb']) })
    pick(container)
    await waitFor(() =>
      expect(chipNames(container)).toEqual(['PocketPay Wallet', 'Riverside Bank']),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Remove PocketPay Wallet' }))
    await waitFor(() => expect(chipNames(container)).toEqual(['Riverside Bank']))
  })

  it('sends the whole chip set to the server on preview, anchor first', async () => {
    const onPreview = vi.fn().mockResolvedValue(preview())
    const { container } = open({ onPreview, onSourcePeek: peekWith(['pocket', 'rvb']) })
    pick(container)
    await waitFor(() =>
      expect(chipNames(container)).toEqual(['PocketPay Wallet', 'Riverside Bank']),
    )
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    const call = onPreview.mock.calls[0]
    expect(call).toBeDefined()
    if (!call) throw new Error('expected onPreview to be called')
    expect(call[2]).toBe('pocket') // the anchor the pipeline routes against
    expect(call[8]).toEqual(['pocket', 'rvb']) // the set the server remembers
  })

  it('a modal with no peek wired still previews, sending just the picked account', async () => {
    const onPreview = vi.fn().mockResolvedValue(preview())
    const { container } = open({ onPreview })
    pick(container)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Select account' })).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Select account' }))
    fireEvent.click(await screen.findByRole('option', { name: /PocketPay Wallet/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]?.[8]).toEqual(['pocket'])
  })
})
