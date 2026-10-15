// @vitest-environment jsdom
//
// Regression: a Lucius statement upload returned a PARTIAL preview ({drafts, source})
// missing skipped / anomaly / per-draft flags. The preview render then read
// `preview.skipped.length` / `preview.anomaly.flagged_rows` / `draft.flags.map` → crashed
// the whole Accounts tab ("Cannot read properties of undefined (reading 'length')"). The
// render must degrade gracefully on a partial preview.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

afterEach(cleanup)

const acct = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  institution: 'Anchor',
  currency: 'SGD',
  creditLimit: null,
  lastFour: null,
  color: '#22d3ee',
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
} as unknown as Account

describe('ImportModal — partial Lucius preview', () => {
  it('renders a preview missing skipped/anomaly/flags without crashing', async () => {
    // The OLD broken shape: only drafts + source (no skipped, no anomaly, no draft.flags).
    const partial = {
      drafts: [
        {
          date: '2026-06-01',
          payee: 'Coffee',
          description: 'COFFEE',
          amount: -5,
          currency: 'SGD',
          dup: false,
        },
      ],
      source: 'pdf',
    }
    const onPreview = vi.fn().mockResolvedValue(partial)
    render(
      <ImportModal
        open
        accounts={[acct]}
        defaultAccountId="a1"
        onClose={() => {}}
        onPreview={onPreview as never}
        onDecisionCommit={async () => ({ created: 0, merged: 0, duplicates: 0, skipped: 0 })}
      />,
    )
    // Source step → pick a PDF file (no client-side parse path).
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['%PDF-1.4 fake'], 'statement.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })
    // Choose step → trigger the Lucius import.
    const luBtn = await screen.findByText(/Read the statement/i)
    fireEvent.click(luBtn)
    // Preview step renders the draft — and did NOT throw on the missing fields.
    await waitFor(() => expect(screen.getByText('COFFEE')).toBeTruthy())
    const disclosure = screen.getByRole('button', { name: /Coffee, Import,.*Expand details/i })
    expect(disclosure.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(disclosure)
    expect(disclosure.getAttribute('aria-expanded')).toBe('true')
    // The metaline degrades gracefully: a missing `total` falls back to the draft count for
    // the rows-read fact, and zero skipped/already-imported render no fact at all.
    expect(screen.getByText('1 row read')).toBeTruthy()
    expect(screen.queryByText(/skipped/i)).toBeNull()
  })

  it('clears disclosure state when the modal resets', async () => {
    const partial = {
      drafts: [
        {
          date: '2026-06-01',
          payee: 'Coffee',
          description: 'COFFEE',
          amount: -5,
          currency: 'SGD',
          dup: false,
        },
      ],
      source: 'pdf',
    }
    const onPreview = vi.fn().mockResolvedValue(partial)
    render(
      <ImportModal
        open
        accounts={[acct]}
        defaultAccountId="a1"
        onClose={() => {}}
        onPreview={onPreview as never}
        onDecisionCommit={async () => ({ created: 0, merged: 0, duplicates: 0, skipped: 0 })}
      />,
    )
    const upload = async () => {
      const input = document.body.querySelector('input[type="file"]') as HTMLInputElement
      fireEvent.change(input, {
        target: { files: [new File(['x'], 'statement.pdf', { type: 'application/pdf' })] },
      })
      fireEvent.click(await screen.findByText(/Read the statement/i))
      return screen.findByRole('button', { name: /Coffee, Import,.*Expand details/i })
    }
    const first = await upload()
    fireEvent.click(first)
    expect(first.getAttribute('aria-expanded')).toBe('true')
    // Closing on the Decide step now confirms first.
    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Discard review' }))
    const second = await upload()
    expect(second.getAttribute('aria-expanded')).toBe('false')
  })
})
