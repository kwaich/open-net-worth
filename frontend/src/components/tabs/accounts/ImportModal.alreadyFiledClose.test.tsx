// @vitest-environment jsdom
//
// Owner, 2026-10-06: re-opening a statement that is ALREADY filed, where every row is already in
// the ledger, offered "File statement — nothing new" — a commit that wrote nothing but still
// recorded an empty import batch. With nothing to confirm, the footer now offers Close and never
// commits. A NEW file whose rows are all already booked keeps "File statement — nothing new":
// filing it still adds the document to the account's statement history.

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account, Category } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn().mockResolvedValue({ ok: false }) }))

afterEach(cleanup)
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

const card = {
  id: 'a1',
  name: 'Northgate Rewards Card',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
} as unknown as Account
const catList: Category[] = []

const dupDraft = {
  date: '2026-06-23',
  payee: 'Vercel',
  description: 'VERCEL INC',
  amount: -20.1,
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: true,
  dupOf: 't_existing',
  rowIndex: 0,
  external_refs: [{ source: 'statement', ref: 'abc123' }],
  flags: [],
}

function preview(alreadyImported: Record<string, unknown> | null) {
  return {
    statementId: 's1',
    drafts: [dupDraft],
    importable: 0,
    duplicates: 1,
    skipped: [],
    total: 1,
    anomaly: {
      near_duplicate: 0,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 0,
    },
    reconcile: { status: 'ok' },
    ledgerCheck: { status: 'ok', rows: [], boundary: [] },
    alreadyImported,
  }
}

async function openWith(previewResult: Record<string, unknown>) {
  const onClose = vi.fn()
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 1 })
  const onPreview = vi.fn().mockResolvedValue(previewResult)
  render(
    <ImportModal
      open
      accounts={[card]}
      defaultAccountId="a1"
      categories={catList}
      catMap={{}}
      onClose={onClose}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
  return { onClose, onDecisionCommit }
}

describe('Decide footer when the review would write nothing', () => {
  it('an already-filed file offers Close, which closes without committing', async () => {
    const { onClose, onDecisionCommit } = await openWith(
      preview({ statement_id: 's1', imported_at: '2026-10-06T00:00:00Z', batch_ids: ['b1'] }),
    )

    // The footer's own action — not the modal's ✕, which is also labelled "Close".
    await screen.findByText('Already filed')
    const close = document.querySelector('.imp3-foot .imp3-commit') as HTMLButtonElement
    expect(close.textContent).toBe('Close')
    expect(close.disabled).toBe(false)
    expect(screen.queryByText('File statement — nothing new')).toBeNull()
    expect(screen.getByText('Already filed')).toBeTruthy()

    fireEvent.click(close)
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(onDecisionCommit).not.toHaveBeenCalled()
  })

  it('a new file whose rows are all already booked still offers to file it', async () => {
    await openWith(preview(null))

    expect(await screen.findByText('File statement — nothing new')).toBeTruthy()
    expect(screen.queryByText('Already filed')).toBeNull()
  })
})
