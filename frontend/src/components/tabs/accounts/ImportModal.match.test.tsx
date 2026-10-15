// @vitest-environment jsdom
//
// The import-dedup 3-way flow (RFC batch-aware-dedup — three-state review): a draft the server
// matched to an existing ledger row carries a near_duplicate/transfer_pair flag with existing_id
// PLUS a band ('strong'|'review'). A REVIEW-band match (this file's fixtures — no `band` set,
// so matchBand()'s documented fallback treats it as 'review') keeps its question treatment and
// defaults to the profile's `import_review_default` (owner 2026-07-17, second decision: factory
// default is MERGE for all users — "let's set default for all user to merge"; skip is the
// explicit opt-out); the user can switch any row to Merge, Skip or Import new before commit.
// Strong-band rendering (calm "will link" default-merge treatment) is covered by
// ImportModal.threeState.test.tsx.
//
// Commit is the DECISION flow for every route (the one server pipeline): the payload sent to
// onDecisionCommit carries per-row deviations only — an unmatched clean row rides the server's
// default create with NO decision entry.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

// The handoff puts a row's OVERRIDE controls inside its disclosure ("tapping a row is only
// ever for overriding, never for discovering"), so a locator that hands back a row for
// interaction opens it first. Idempotent: an already-open row is left alone.
const openRow = (row: HTMLElement): HTMLElement => {
  const line = row.querySelector('.imp3-arow-line') as HTMLElement | null
  if (line && line.getAttribute('aria-expanded') === 'false') fireEvent.click(line)
  return row
}

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))

afterEach(cleanup)

const acct = {
  id: 'a1',
  name: 'Anchor',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
  color: '#22d3ee',
} as unknown as Account

function previewWith(flags: unknown) {
  return {
    statementId: 'st-match',
    drafts: [
      {
        date: '2026-05-21',
        payee: 'Cinebar',
        description: 'MPOS*CINEBAR',
        amount: -11.04,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 0,
        flags,
        external_refs: [{ source: 'pdf', ref: 'ref:a1:747' }],
      },
      {
        date: '2026-05-22',
        payee: 'Lazada',
        description: 'LAZADA SG',
        amount: -6.89,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 1,
        flags: [],
      },
    ],
    importable: 2,
    duplicates: 0,
    skipped: [],
    total: 2,
    anomaly: {
      near_duplicate: 1,
      transfer_pair: 0,
      intra_batch_duplicate: 0,
      amount_anomaly: 0,
      flagged_rows: 1,
    },
  }
}

async function openPreview(
  onDecisionCommit: ReturnType<typeof vi.fn>,
  extra: Record<string, unknown> = {},
) {
  const matchFlag = [
    {
      type: 'near_duplicate',
      reason: 'match',
      existing_id: 'existing-1',
      existing: { note: 'Cinebar night out', amount: -11.0, currency: 'SGD', has_category: true },
    },
  ]
  const onPreview = vi.fn().mockResolvedValue(previewWith(matchFlag))
  const { container } = render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit as never}
      {...extra}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  fireEvent.change(fileInput, {
    target: { files: [new File(['%PDF-1.4'], 's.pdf', { type: 'application/pdf' })] },
  })
  fireEvent.click(await screen.findByText(/Read the statement/i))
  await waitFor(() => expect(screen.getByText('LAZADA SG')).toBeTruthy())
  return container
}

const commitMock = () =>
  vi.fn().mockResolvedValue({ created: 1, merged: 1, duplicates: 0, skipped: 0 })

// The decision radiogroup lives inside the matched row's disclosure (handoff: "tapping a row
// is only ever for overriding, never for discovering"), so reach it through the open row.
// Located by its stable data-draft-index (row 0, the near-duplicate match) — the review-band
// flag headline ("Already in your account?" + its raw reason bits) is hidden as of 2026-07-28
// (owner: "isn't that we decided to not show anything like this?"), and the statement-side
// name ("Cinebar") isn't unique once the row expands (the comparison card repeats it).
const matchedRow = () => openRow(document.querySelector('[data-draft-index="0"]') as HTMLElement)
const decisionRadio = (name: string) => within(matchedRow()).getByRole('radio', { name })

describe('ImportModal — import-dedup 3-way (decision commit)', () => {
  it('review row with import_review_default=merge: default commit sends the merge decision, the clean row rides the server default', async () => {
    const onDecisionCommit = commitMock()
    await openPreview(onDecisionCommit, { importReviewDefault: 'merge' })
    // Matched row renders as a review-band pair row (3-way control), Merge pre-selected (the
    // profile opt-in ladder: strong → merge, review → the setting).
    expect(screen.getByText('Cinebar')).toBeTruthy()
    expect(decisionRadio('Leave out')).toBeTruthy()
    expect(decisionRadio('Link').className).toContain('on')
    // Footer: the matched row LINKS by default, the unmatched row imports.
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![0]).toBe('st-match')
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'existing-1' },
    ])
  })

  it('an explicit Skip parks the matched row: the decision says skip, nothing merges', async () => {
    const onDecisionCommit = commitMock()
    await openPreview(onDecisionCommit)
    fireEvent.click(decisionRadio('Leave out'))
    const compactTrigger = screen.getByRole('button', { name: /Cinebar, Leave out,/i })
    const compactRow = openRow(compactTrigger.closest('.imp-pair-row') as HTMLElement)
    expect(compactRow).toBeTruthy()
    expect(within(compactRow).getByText('Leave out', { selector: '.imp3-dchip' })).toBeTruthy()
    const categoryColumn = compactRow.querySelector('.imp3-arow-catcol') as HTMLElement
    // the handoff hides the category entirely on a leave-out row — nothing books, nothing to categorise
    expect(categoryColumn.textContent?.trim()).toBe('')
    expect(categoryColumn.querySelector('i')).toBeNull()
    expect(within(categoryColumn).queryByText(/Uncategorized|Groceries|Transfer/i)).toBeNull()
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([{ row: 0, action: 'skip' }])
  })

  it('Merge folds the matched row into the existing transaction via the decision payload', async () => {
    const onDecisionCommit = commitMock()
    await openPreview(onDecisionCommit)
    fireEvent.click(decisionRadio('Link'))
    // Footer grammar (§7.2): a merge counts toward LINKS, not imports — "Link 1 · import 1".
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'existing-1' },
    ])
  })

  it('Import new sends an explicit create decision for the matched row', async () => {
    const onDecisionCommit = commitMock()
    await openPreview(onDecisionCommit)
    fireEvent.click(decisionRadio('Import'))
    fireEvent.click(await screen.findByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([{ row: 0, action: 'create' }])
  })
})
