// @vitest-environment jsdom
//
// Manual link (owner 2026-07-18, verbatim intent: "let the user decide to merge these two by
// themselves before import — not via the dedup engine — a UI that lets the user choose the
// one"). The motivating case: a Fling charge the email agent booked as "TD subscription renewal"
// −₫260,000 on 06-11, and the statement row "example-subscription…" −₫250,100 on 06-14 — the same charge,
// 3 days + ~3.8% apart, below every auto-match gate, so the statement row lands in "New" while
// the ledger entry idles in "not on statement" with no way to link them. This adds that way.
//
// A picked link flows through the EXISTING matched-row machinery: the New row becomes a
// review-band pair card defaulting to Merge, its AFTER IMPORT column projects the linked
// entry, the two-question verdict + Off-by surfaces light up, the entry drops off the "not on
// statement" panel pre-commit, and the commit payload carries {action:'merge', existing_id}.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account } from './types'

// The ledger section's count lives in its own badge (the design's imp3-sec-badge); the title no
// longer repeats it, so read the number from the badge.
const ledgerSectionCount = () =>
  document.querySelector('.imp3-sec.ledger .imp3-sec-badge')?.textContent ?? null

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response) // the post-commit ledger-diff probe
})

// VND (a zero-decimal currency) — matches the Fling case above, and fCur renders it
// without cents so the assertions stay exact (₫260,000, not ₫260,000.00).
const acct = {
  id: 'v1',
  name: 'Harborline Bank',
  kind: 'asset',
  type: 'cash',
  currency: 'VND',
  color: '#22d3ee',
} as unknown as Account
const base = {
  currency: 'VND',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

// The verdict headline ('✓ Will link', /Linked by you/) that used to identify a matched row
// is gone (owner, 2026-07-29 — it trailed raw matcher diagnostics). These fixtures have at
// most ONE matched pair card, so address it directly; a plain row stays a bare `.imp3-arow`.
const pairRowEl = () => document.querySelector('.imp-pair-row') as HTMLElement | null
const pairRow = () => openRow(pairRowEl() as HTMLElement)
// An unmatched (New) row's Link-to-existing lives in its disclosure, like every other row's
// override controls — open the row to reach it.
const newRow = (text: string | RegExp = 'example-subscription.com Fling') =>
  openRow(screen.getByText(text).closest('.imp3-arow') as HTMLElement)
const linkBtn = (text?: string | RegExp) =>
  within(newRow(text)).getByRole('button', { name: /Link to existing/ })
const afterCol = (row: HTMLElement) => row.querySelector('.imp3-after') as HTMLElement

async function openWith(preview: unknown, extra: Record<string, unknown> = {}) {
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })
  const onPreview = vi.fn().mockResolvedValue(preview)
  const utils = render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="v1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit}
      {...extra}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  return utils
}

// The Fling case: ONE New statement row (the charge the engine never matched) + two uncovered
// ledger entries (the agent-booked twin the user wants to link, plus a decoy so the filter has
// something to narrow away).
function tinderPreview(extra: Record<string, unknown> = {}) {
  return {
    drafts: [
      {
        ...base,
        date: '2026-06-14',
        payee: 'gofling',
        description: 'example-subscription.com Fling',
        amount: -250100,
        external_refs: [{ source: 'pdf', ref: 'ref:v1:t1' }],
        rowIndex: 0,
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
    ledgerCheck: {
      period: { start: '2026-06-01', end: '2026-06-30' },
      rows: [
        {
          id: 'led-fling',
          date: '2026-06-11',
          note: 'TD subscription renewal',
          amount: -260000,
          currency: 'VND',
          kind: 'expense',
          source: 'agent',
        },
        {
          id: 'led-elec',
          date: '2026-06-09',
          note: 'Electricity bill',
          amount: -430000,
          currency: 'VND',
          kind: 'expense',
          source: 'manual',
        },
      ],
      boundary: [],
    },
    ...extra,
  }
}

// A New row PLUS a strong engine match — the matched row must never offer the manual-link
// affordance (it is already resolved).
function matchedAndNewPreview() {
  const p = tinderPreview()
  p.drafts.push({
    ...base,
    date: '2026-06-20',
    payee: 'Grab',
    description: 'GRAB',
    amount: -80000,
    external_refs: [{ source: 'pdf', ref: 'ref:v1:g1' }],
    rowIndex: 1,
    flags: [
      {
        type: 'near_duplicate',
        reason: 'sure',
        existing_id: 'e-grab',
        band: 'strong',
        existing: {
          note: 'Grab ride',
          amount: -80000,
          currency: 'VND',
          date: '2026-06-20',
          kind: 'expense',
        },
      },
    ],
  } as (typeof p.drafts)[number])
  p.importable = 2
  p.total = 2
  return p
}

describe('ImportModal — the ⇄ Link affordance', () => {
  it('appears on a New row and NOT on an already-matched row', async () => {
    await openWith(matchedAndNewPreview())
    await screen.findByText('example-subscription.com Fling')
    expect(linkBtn()).toBeTruthy() // the New row offers it
    const matchedRow = pairRow() // the strong match's pair card
    expect(within(matchedRow).queryByRole('button', { name: /Link to existing/ })).toBeNull()
  })
})

describe('ImportModal — the manual-link picker', () => {
  it('lists every uncovered entry and filters by a typed term', async () => {
    await openWith(tinderPreview())
    await screen.findByText('example-subscription.com Fling')
    fireEvent.click(linkBtn())
    const list = screen.getByRole('listbox', { name: 'Link this row to an existing ledger entry' })
    const opts = within(list).getAllByRole('option')
    expect(opts.length).toBe(2)
    const text = opts.map((o) => o.textContent).join(' | ')
    expect(text).toContain('TD subscription renewal')
    expect(text).toContain('Electricity bill')
    // A short list needs no filter (the design shows the search only past 4 options) — the
    // two entries are simply both listed. Filtering is covered on a long list below.
  })
})

describe('ImportModal — picking a link', () => {
  async function pickFling(
    extra: Record<string, unknown> = {},
    props: Record<string, unknown> = {},
  ) {
    await openWith(tinderPreview({ statementId: 'st-fling', ...extra }), props)
    await screen.findByText('example-subscription.com Fling')
    // Before the link: the entry sits in the "not on statement" panel (2 entries).
    expect(ledgerSectionCount()).toBe('2')
    fireEvent.click(linkBtn())
    fireEvent.click(screen.getByRole('option', { name: /TD subscription renewal/ }))
  }

  it('turns the row into a matched Merge — After import shows the linked entry, the panel drops it', async () => {
    await pickFling()
    const row = pairRow()
    // Defaults to Merge (the profile default ladder, factory = merge).
    expect(within(row).getByRole('radio', { name: 'Link' }).getAttribute('aria-checked')).toBe(
      'true',
    )
    // The AFTER IMPORT column projects the merge INTO the linked entry.
    expect(within(afterCol(row)).getByText('TD subscription renewal')).toBeTruthy()
    // The now-accounted entry left the "not on statement" panel pre-commit (2 → 1).
    expect(ledgerSectionCount()).toBe('1')
    expect(screen.queryByText(/— 2$/)).toBeNull()
  })

  it('the commit payload carries {row, action:merge, existing_id} for the linked entry', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await pickFling({}, { onDecisionCommit })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'led-fling' },
    ])
  })

  it('Leave out → Link clears the stale skip and commits the explicit merge', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await openWith(tinderPreview({ statementId: 'st-fling' }), { onDecisionCommit })
    const plainRow = openRow(
      openRow((await screen.findByText('gofling')).closest('.imp3-arow') as HTMLElement),
    )
    fireEvent.click(within(plainRow).getByRole('radio', { name: 'Leave out' }))
    fireEvent.click(within(plainRow).getByRole('button', { name: /Link to existing/ }))
    fireEvent.click(screen.getByRole('option', { name: /TD subscription renewal/ }))

    expect(pairRowEl()).toBeTruthy() // the row is now a matched pair card
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'led-fling' },
    ])
  })

  it('a decided manual-link row can still switch to a different choice', async () => {
    // REWORDED 2026-07-27/28 (owner: "remove this and relevant code" — the 1/2/3 keyboard
    // shortcut queue is gone; "when i tap on any decision, they should move to their
    // respective list" — a manual link decides the row immediately, so it never renders as
    // a Needs-you queue card at all, it's a regular matched pair row throughout — no "Undo"
    // button there, just re-picking a different option).
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await pickFling({}, { onDecisionCommit })
    const pair = pairRow()
    fireEvent.click(within(pair).getByRole('radio', { name: 'Import' }))
    expect(within(pair).getByRole('radio', { name: 'Import' }).getAttribute('aria-checked')).toBe(
      'true',
    )
    expect(within(pair).getByRole('radio', { name: /Link/ }).getAttribute('aria-checked')).toBe(
      'false',
    )

    fireEvent.click(within(pair).getByRole('radio', { name: /Link/ }))
    expect(within(pair).getByRole('radio', { name: /Link/ }).getAttribute('aria-checked')).toBe(
      'true',
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'led-fling' },
    ])
  })

  it('a manually-linked row can change its ledger match via Unlink, then relinking', async () => {
    // REWORDED 2026-07-28: manual-link rows never expose "Change match" directly in the
    // matched-pair disclosure (`!manualLinked` gate) — that affordance only ever existed on
    // the old Needs-you queue card, which this row no longer renders as (a manual link
    // decides it immediately, per "when i tap on any decision, they should move to their
    // respective list"). Unlink + "Link to existing" is the real path to a different match.
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 0, merged: 1, duplicates: 0, skipped: 0 })
    await pickFling({}, { onDecisionCommit })
    const pair = pairRow()

    fireEvent.click(within(pair).getByRole('button', { name: /Unlink/ }))
    fireEvent.click(within(pair).getByRole('button', { name: /Link to existing/ }))
    fireEvent.click(screen.getByRole('option', { name: /Electricity bill/ }))
    expect(pairRowEl()).toBeTruthy() // the row is now a matched pair card

    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalledTimes(1))
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'led-elec' },
    ])
  })

  it('unlink reverts the row to a plain New import — the card is gone, no merge decision commits', async () => {
    const onDecisionCommit = vi
      .fn()
      .mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 })
    await pickFling({}, { onDecisionCommit })
    fireEvent.click(within(pairRow()).getByRole('button', { name: /Unlink/ }))
    // Back to a plain New row: the matched card is gone, the affordance is offered again, and
    // the entry returned to the panel.
    expect(pairRowEl()).toBeNull() // unlinked — back to a plain row
    expect(linkBtn()).toBeTruthy()
    expect(ledgerSectionCount()).toBe('2')
    // Commit: the row imports as new (a create / no decision entry), never a merge.
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    const decisions = onDecisionCommit.mock.calls[0]![2] as Array<Record<string, unknown>>
    expect(decisions.some((x) => x.row === 0 && x.action === 'merge')).toBe(false)
  })

  it('a drift on the manual link surfaces the Off-by marker (kept via Skip)', async () => {
    await pickFling()
    // Default Merge adopts the statement's settled figure (same-kind expense) → no drift.
    expect(pairRow().querySelector('.imp-offby')).toBeNull()
    // Skip keeps YOUR −₫260,000 standing against the statement's −₫250,100 → Off by ₫9,900.
    fireEvent.click(within(pairRow()).getByRole('radio', { name: 'Leave out' }))
    expect(pairRow().querySelector('.imp-offby')!.textContent).toBe('Off by ₫9,900')
  })

  it('a long list gains the search box and narrows to the typed term', async () => {
    // 5 uncovered entries — past the design's 4-option threshold for showing the search.
    const long = tinderPreview()
    const rows = (long.ledgerCheck as Record<string, unknown>).rows as Array<
      Record<string, unknown>
    >
    rows.push(
      { id: 'led-x1', date: '2026-06-02', note: 'Gym membership', amount: -50000, currency: 'VND' },
      {
        id: 'led-x2',
        date: '2026-06-03',
        note: 'Coffee subscription',
        amount: -60000,
        currency: 'VND',
      },
      { id: 'led-x3', date: '2026-06-04', note: 'Water bill', amount: -70000, currency: 'VND' },
    )
    await openWith(long)
    await screen.findByText('example-subscription.com Fling')
    fireEvent.click(linkBtn())
    const list = screen.getByRole('listbox', { name: 'Link this row to an existing ledger entry' })
    expect(within(list).getAllByRole('option').length).toBe(5)
    fireEvent.change(within(list).getByPlaceholderText(/Search entries/), {
      target: { value: 'TD subscription' },
    })
    const filtered = within(list).getAllByRole('option')
    expect(filtered.length).toBe(1)
    expect(filtered[0]!.textContent).toContain('TD subscription renewal')
  })
})

describe('ImportModal — reverse direction (from the ledger-only panel)', () => {
  it('a "not on statement" entry links an unmatched statement row to itself', async () => {
    await openWith(tinderPreview({ statementId: 'st-fling' }))
    await screen.findByText('example-subscription.com Fling')
    // Open the reverse picker on the Fling entry's ledger-only row.
    const entryRow = openRow(
      screen
        .getByRole('button', { name: 'TD subscription renewal' })
        .closest('.imp3-arow') as HTMLElement,
    )
    fireEvent.click(within(entryRow).getByRole('button', { name: /Link to a new row/ }))
    const list = screen.getByRole('listbox', { name: 'Link a statement row to this entry' })
    // It offers the unmatched New row; pick it.
    fireEvent.click(within(list).getByRole('option', { name: /gofling/ }))
    // The statement row is now a matched pair card, and the entry left the panel (2 → 1).
    expect(pairRowEl()).toBeTruthy() // the row is now a matched pair card
    expect(ledgerSectionCount()).toBe('1')
  })
})
