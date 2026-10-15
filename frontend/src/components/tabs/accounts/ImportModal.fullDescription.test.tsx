// @vitest-environment jsdom
//
// A row cannot be categorized from text you cannot read (owner, 2026-08-09, Riverside Bank July:
// "i can't see the full text of the row so it's hard to categorize them by myself ... basically
// there must be a way that users can see the entire description").
//
// The list row's name sits in a FIXED-width rail and hard-ellipsizes, and a native hover
// tooltip is not a way to read fourteen rows. So the complete raw descriptor is restated,
// WRAPPED, in the row's expanded body — and every row expands to reach it, duplicates
// included (a dup row used to have no expandable body at all, which left its text unreachable
// by any means).

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

vi.mock('../../../api/client', () => ({
  authFetch: vi.fn(async () => ({ ok: true, json: async () => ({}) })),
}))

afterEach(() => cleanup())

const acct = {
  id: 'a1',
  name: 'Riverside Bank',
  kind: 'asset',
  type: 'cash',
  currency: 'VND',
} as unknown as Account
const CSV = 'Date,Description,Amount\n2026-07-01,X,-523000\n'

// A realistically long bank descriptor: far past what the 252px name rail can show.
const LONG = 'MBRVB.10000000000.1000000.2.CHUYEN TIEN NHANH LIEN NGAN HANG QUA THE'
const DUP = 'UHHT..100000..20260629. .100000.CGA HOAN TIEN GIAO DICH THE'

const draft = (i: number, description: string, dup: boolean) => ({
  date: '2026-07-01',
  payee: null,
  description,
  amount: -523000,
  currency: 'VND',
  kind: 'expense',
  category: null,
  dup,
  dupOf: dup ? 'txn-old' : undefined,
  rowIndex: i,
  external_refs: [{ source: 'csv', ref: `c${i}` }],
  flags: [],
})

const PREVIEW = {
  statementId: 'st-1',
  drafts: [draft(0, LONG, false), draft(1, DUP, true)],
  importable: 1,
  duplicates: 1,
  skipped: [],
  total: 2,
  anomaly: {
    near_duplicate: 0,
    transfer_pair: 0,
    intra_batch_duplicate: 0,
    amount_anomaly: 0,
    flagged_rows: 0,
  },
  instructions: null,
}

async function mountPreview() {
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={vi.fn() as never}
      onPreview={vi.fn().mockResolvedValue(PREVIEW) as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 1, skipped: 0 }) as never
      }
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], 'riverside.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  fireEvent.change(fileInput, { target: { files: [file] } })
  fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))
  await screen.findByRole('button', { name: /Confirm/ })
}

function rowAt(index: number) {
  return document.body.querySelector(`[data-draft-index="${index}"]`) as HTMLElement
}

describe('ImportModal — the entire description is always reachable', () => {
  it('expanding a row restates the descriptor in full, not truncated', async () => {
    await mountPreview()
    const row = rowAt(0)
    // Collapsed, the only copy of the text is the ellipsizing name rail.
    expect(row.querySelector('.imp3-arow-full')).toBeNull()

    fireEvent.click(row.querySelector('.imp3-arow-line') as HTMLElement)

    const full = rowAt(0).querySelector('.imp3-arow-full') as HTMLElement
    expect(full).toBeTruthy()
    // The WHOLE string — an assertion on the exact text, so a future truncation fails here.
    expect(full.textContent).toBe(LONG)
    expect(full.textContent).not.toContain('…')
  })

  it('a duplicate row expands too — its text was previously unreachable by any means', async () => {
    await mountPreview()
    const row = rowAt(1)
    expect(row.querySelector('.imp3-arow-line')!.getAttribute('aria-expanded')).toBe('false')

    fireEvent.click(row.querySelector('.imp3-arow-line') as HTMLElement)

    expect((rowAt(1).querySelector('.imp3-arow-full') as HTMLElement).textContent).toBe(DUP)
  })

  it('a SHORT descriptor is not restated — the row already shows it in full', async () => {
    // The regression this guards: restating unconditionally printed the same string twice on
    // every ordinary row (the name rail already carries it), which broke five sibling tests
    // with "found multiple elements" and would have read as a stutter to the owner.
    render(
      <ImportModal
        open
        accounts={[acct]}
        defaultAccountId="a1"
        onClose={vi.fn() as never}
        onPreview={
          vi.fn().mockResolvedValue({
            ...PREVIEW,
            drafts: [draft(0, 'ExampleSub', false)],
            duplicates: 0,
            total: 1,
          }) as never
        }
        onDecisionCommit={vi.fn() as never}
      />,
    )
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File([CSV], 'riverside.csv', { type: 'text/csv' })
    Object.defineProperty(file, 'text', { value: async () => CSV })
    fireEvent.change(fileInput, { target: { files: [file] } })
    fireEvent.click(await screen.findByRole('button', { name: /Read the statement/ }))
    await screen.findByRole('button', { name: /Confirm/ })

    fireEvent.click(rowAt(0).querySelector('.imp3-arow-line') as HTMLElement)

    expect(rowAt(0).querySelector('.imp3-arow-full')).toBeNull()
    expect(screen.getAllByText('ExampleSub').length).toBe(1)
  })

  it('the chevron is never hidden — every row advertises that it opens', async () => {
    await mountPreview()
    for (const index of [0, 1]) {
      const chev = rowAt(index).querySelector('.imp3-chev') as SVGElement
      expect(chev).toBeTruthy()
      expect((chev as unknown as HTMLElement).style.visibility).toBe('')
    }
  })
})
