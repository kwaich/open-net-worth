// @vitest-environment jsdom
//
// An incomplete statement parse (or one whose own balances don't reconcile) must never let
// the "In your ledger, not on this statement" band claim rows are missing — the server withholds
// `ledgerCheck` entirely (`ledgerCheck.withheld`) instead of computing it partial. This pins the
// frontend half: no band, no bulk-delete button, no "Not on statement" chip, and one amber notice
// with a working re-read action. See
// docs/adr/2026-09-09-an-incomplete-parse-withholds-the-ledger-diff.md.

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

async function openWith(onPreview: ReturnType<typeof vi.fn>) {
  render(
    <ImportModal
      open
      accounts={[card]}
      defaultAccountId="a1"
      categories={catList}
      catMap={{}}
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={vi
        .fn()
        .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
}

const baseDraft = {
  date: '2026-06-23',
  payee: 'Vercel',
  description: 'VERCEL INC',
  amount: -20.1,
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  rowIndex: 0,
  external_refs: [{ source: 'pdf', ref: 'ref:a1:vercel' }],
  flags: [],
}

function basePreview(ledgerCheck: Record<string, unknown>, reconcile?: Record<string, unknown>) {
  return {
    drafts: [baseDraft],
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
    reconcile: reconcile ?? { status: 'ok' },
    ledgerCheck,
  }
}

const assertNoBandOrChip = () => {
  expect(screen.queryByText(/In your ledger, not on this statement/i)).toBeNull()
  expect(screen.queryByText(/Mark all \d+ for deletion/i)).toBeNull()
  expect(screen.queryByText('Not on statement')).toBeNull()
  expect(document.querySelector('.imp-pair-bandhead')).toBeNull()
}

describe('withheld ledger check — parse_incomplete', () => {
  it('shows the amber notice with the exact copy, no band, no chip', async () => {
    const onPreview = vi
      .fn()
      .mockResolvedValue(basePreview({ withheld: 'parse_incomplete', pages_incomplete: 2 }))
    await openWith(onPreview)

    expect(await screen.findByText('Can’t check what’s already in your ledger yet')).toBeTruthy()
    expect(
      screen.getByText(
        '2 pages of this statement didn’t finish reading, so we can’t tell what’s missing ' +
          'from it yet. The rows we did read are shown above and are safe to import — re-read ' +
          'the file to try the incomplete pages again.',
      ),
    ).toBeTruthy()
    assertNoBandOrChip()
  })

  it('singularizes "page" for exactly one incomplete page', async () => {
    const onPreview = vi
      .fn()
      .mockResolvedValue(basePreview({ withheld: 'parse_incomplete', pages_incomplete: 1 }))
    await openWith(onPreview)

    expect(
      await screen.findByText(
        '1 page of this statement didn’t finish reading, so we can’t tell what’s missing ' +
          'from it yet. The rows we did read are shown above and are safe to import — re-read ' +
          'the file to try the incomplete page again.',
      ),
    ).toBeTruthy()
  })

  it('re-read action re-runs the preview with forceReparse', async () => {
    const onPreview = vi
      .fn()
      .mockResolvedValueOnce(basePreview({ withheld: 'parse_incomplete', pages_incomplete: 3 }))
      .mockResolvedValueOnce(basePreview({ rows: [], boundary: [] }))
    await openWith(onPreview)

    fireEvent.click(await screen.findByText('Re-read the file'))
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(2))
    const secondCall = onPreview.mock.calls[1]!
    expect(secondCall[6]).toBe(true) // forceReparse is the 7th positional arg
  })
})

describe('withheld ledger check — reconcile_off', () => {
  it('shows the amber notice with the delta, no band, no chip', async () => {
    const onPreview = vi
      .fn()
      .mockResolvedValue(
        basePreview(
          { withheld: 'reconcile_off', pages_incomplete: 0, delta: -302.17, currency: 'SGD' },
          { status: 'off', delta: -302.17, currency: 'SGD' },
        ),
      )
    await openWith(onPreview)

    expect(await screen.findByText('Can’t check what’s already in your ledger yet')).toBeTruthy()
    expect(
      screen.getByText(
        'This statement is off by S$302.17 against its own balances, so we can’t trust a ' +
          'ledger-only comparison yet. The rows above are still safe to import — re-read the ' +
          'file, or double-check the statement’s totals.',
      ),
    ).toBeTruthy()
    assertNoBandOrChip()
  })
})

describe('withheld ledger check — parse_unverified', () => {
  it('shows the amber notice with the legacy-record copy, no band, no chip', async () => {
    const onPreview = vi
      .fn()
      .mockResolvedValue(basePreview({ withheld: 'parse_unverified', pages_incomplete: 0 }))
    await openWith(onPreview)

    expect(await screen.findByText('Can’t check what’s already in your ledger yet')).toBeTruthy()
    expect(
      screen.getByText(
        'This statement was read before completeness checks existed, so we can’t tell ' +
          'what’s missing from it yet. The rows are still safe to import — re-read the file ' +
          'to enable the ledger comparison.',
      ),
    ).toBeTruthy()
    assertNoBandOrChip()
  })

  it('re-read action re-runs the preview with forceReparse', async () => {
    const onPreview = vi
      .fn()
      .mockResolvedValueOnce(basePreview({ withheld: 'parse_unverified', pages_incomplete: 0 }))
      .mockResolvedValueOnce(basePreview({ rows: [], boundary: [] }))
    await openWith(onPreview)

    fireEvent.click(await screen.findByText('Re-read the file'))
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(2))
    const secondCall = onPreview.mock.calls[1]!
    expect(secondCall[6]).toBe(true) // forceReparse is the 7th positional arg
  })
})

describe('withheld ledger check — regression, an open gate still renders the band', () => {
  it('a normal ledgerCheck with uncovered rows still shows the band and chip', async () => {
    const onPreview = vi.fn().mockResolvedValue(
      basePreview({
        period: { start: '2026-06-01', end: '2026-06-30' },
        rows: [
          {
            id: 'led-1',
            date: '2026-06-15',
            note: 'Some other charge',
            amount: -18.44,
            currency: 'SGD',
            kind: 'expense',
            source: 'ai',
            category: null,
            account_id: 'a1',
          },
        ],
        boundary: [],
      }),
    )
    await openWith(onPreview)

    expect(await screen.findByText('In your ledger, not on this statement')).toBeTruthy()
    expect(screen.getByText('Not on statement')).toBeTruthy()
    expect(screen.queryByText('Can’t check what’s already in your ledger yet')).toBeNull()
  })
})
