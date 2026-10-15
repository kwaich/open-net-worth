// @vitest-environment jsdom
//
// The `Will link` bulk bar: it mutates EXACTLY the rows rendered beneath it.
//
// Owner repro (Northgate, 2026-07-29): the bar read "✓ Will link automatically — 18" above a list
// of 37, and its buttons acted on all 37. "Import all as new" would have created 37 duplicate
// transactions while the label promised 18; "Skip all" would have silently discarded 19
// decisions (manual links, a Lucius-routed transfer, accepted probable matches).
//
// The two halves were computed differently: the label counted `pairBand === 'strong'` (a
// CONFIDENT ENGINE MATCH only), the buttons acted on `effectiveChipKey === 'strong'`
// (everything that ends up linking — engine match, manual link, Lucius force_match, an
// accepted review-band match, an absorbed FX leg). Pre-existing since 736be5c2.
//
// The fix kept the ACTION (a bulk bar above a list of N acts on those N) and made ONE array —
// `visibleStmt`, the rows the list renders — feed both the count and the mutation.
//
// The label is GONE (owner 2026-07-31): the `Will link` section header directly beneath already
// states the count, so the bar is buttons only. That removes the half these tests used to read,
// NOT the invariant — the buttons must still move exactly the rows on screen and no others. So
// the expected count is derived from the RENDERED ROWS themselves, which is the population the
// old label was supposed to be describing all along.

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import { authFetch } from '../../../api/client'
import type { Account } from './types'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  Element.prototype.scrollIntoView = vi.fn()
  mockAuthFetch.mockResolvedValue({ ok: false } as Response) // the post-commit ledger-diff probe
})

const acct = {
  id: 'v1',
  name: 'Northgate',
  kind: 'liability',
  type: 'credit',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account
const base = {
  currency: 'SGD',
  kind: 'expense',
  category: null,
  dup: false,
  preRecon: false,
  autoCat: false,
}

const bandedRow = (rowIndex: number, band: 'strong' | 'review', payee: string) => ({
  ...base,
  date: '2026-06-20',
  payee,
  description: payee.toUpperCase(),
  amount: -10 * (rowIndex + 1),
  external_refs: [{ source: 'pdf', ref: `ref:v1:r${rowIndex}` }],
  rowIndex,
  flags: [
    {
      type: 'near_duplicate',
      reason: 'sure',
      existing_id: `e-${rowIndex}`,
      band,
      existing: {
        note: payee,
        amount: -10 * (rowIndex + 1),
        currency: 'SGD',
        date: '2026-06-20',
        kind: 'expense',
      },
      alternatives: [],
    },
  ],
})

// One CONFIDENT engine match + two rows that also end up linking without being confident
// engine matches (probable matches, pre-selected to link by the profile's review default —
// exactly the population the old label excluded), plus one plain unmatched row so the Will
// link chip is a real subset of the statement.
const mixedPreview = () => ({
  statementId: 'st-northgate',
  drafts: [
    bandedRow(0, 'strong', 'Grab'),
    bandedRow(1, 'review', 'Kestreleats'),
    bandedRow(2, 'review', 'Circles Life'),
    {
      ...base,
      date: '2026-06-21',
      payee: 'Kopitiam',
      description: 'KOPITIAM',
      amount: -4,
      external_refs: [{ source: 'pdf', ref: 'ref:v1:r3' }],
      rowIndex: 3,
      flags: [],
    },
  ],
  importable: 4,
  duplicates: 0,
  skipped: [],
  total: 4,
  anomaly: {
    near_duplicate: 3,
    transfer_pair: 0,
    intra_batch_duplicate: 0,
    amount_anomaly: 0,
    flagged_rows: 3,
  },
  ledgerCheck: { period: { start: '2026-06-01', end: '2026-06-30' }, rows: [], boundary: [] },
})

async function openWith(preview: unknown, extra: Record<string, unknown> = {}) {
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 0, merged: 0, duplicates: 0, skipped: 0 })
  const onPreview = vi.fn().mockResolvedValue(preview)
  render(
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
  return { onDecisionCommit }
}

const bandhead = () => document.querySelector('.imp-pair-bandhead') as HTMLElement | null
/** The rows the list under the bar actually RENDERS — the bar's population, counted directly
 *  off the DOM rather than off any copy that claims to describe it. */
const renderedRows = () => document.querySelectorAll('.imp3-review-list [data-draft-index]').length
/** The number of rows that will LINK on commit — the footer's own outcome sentence. */
const linkCount = () => {
  const m = (document.querySelector('.imp3-foot-note')?.textContent ?? '').match(/Link (\d+)/)
  return m ? Number(m[1]) : 0
}

const openWillLinkChip = async () => {
  await waitFor(() => expect(screen.getByRole('button', { name: /^Will link/ })).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: /^Will link/ }))
  await waitFor(() => expect(bandhead()).toBeTruthy())
}

describe('ImportModal — the Will link bulk bar mutates exactly what it sits above', () => {
  // THE PIN. Both halves are counted in the same test: the number of rows RENDERED beneath the
  // bar, and the number of rows a bulk button actually moves out of the linking population.
  it('a bulk button moves exactly the rows rendered beneath it', async () => {
    await openWith(mixedPreview())
    await openWillLinkChip()

    const onScreen = renderedRows()
    const linksBefore = linkCount()
    expect(linksBefore).toBe(3) // 1 confident engine match + 2 accepted probable matches
    expect(onScreen).toBe(3)

    fireEvent.click(screen.getByRole('button', { name: /^Import all/ }))
    const mutated = linksBefore - linkCount()

    expect(mutated).toBe(onScreen)
  })

  // The exact shape of the original bug: the confident-engine-match subset is 1 of these 3, and
  // a bar that acted on that subset would leave 2 links standing. It must move all 3.
  it('acts on the whole linking population, not the confident-engine-match subset', async () => {
    await openWith(mixedPreview())
    await openWillLinkChip()
    expect(renderedRows()).toBe(3)

    fireEvent.click(screen.getByRole('button', { name: /^Import all/ }))
    expect(linkCount()).toBe(0)
  })

  // Owner scar: "Skip all" silently discarding decisions the owner had already made. It may
  // still do that — that is what a bulk action is — but only over the rows on screen.
  it('leaves out exactly the rows rendered beneath it', async () => {
    await openWith(mixedPreview())
    await openWillLinkChip()
    const onScreen = renderedRows()
    const linksBefore = linkCount()
    fireEvent.click(screen.getByRole('button', { name: /^Leave all out$/ }))
    expect(linksBefore - linkCount()).toBe(onScreen)
  })

  // The bar states no count of its own (owner 2026-07-31) — the section header beneath owns it.
  // A number here is what drifted from the action in the first place.
  it('renders its buttons and nothing else — no headline, no note, no count', async () => {
    await openWith(mixedPreview())
    await openWillLinkChip()
    const head = bandhead()!
    const buttons = Array.from(head.querySelectorAll('button'))
    expect(head.children.length).toBe(buttons.length)
    expect(head.textContent).toBe(buttons.map((b) => b.textContent).join(''))
    expect(head.textContent).not.toMatch(/\d/)
    expect(head.textContent).not.toMatch(/automatic/i)
  })

  // One consistent word per action across the whole app: link, import, leave out.
  it('uses the app-wide action vocabulary, in the same order as the per-row control', async () => {
    await openWith(mixedPreview())
    await openWillLinkChip()
    const labels = Array.from(bandhead()!.querySelectorAll('.imp-seg')).map((b) => b.textContent)
    expect(labels).toEqual(['Link all', 'Import all', 'Leave all out'])
  })
})
