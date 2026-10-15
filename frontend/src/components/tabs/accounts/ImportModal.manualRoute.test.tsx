// @vitest-environment jsdom
//
// SI-65 — the MANUAL column-mapping route rides the ONE server decision pipeline: completing
// the mapping and hitting Preview sends the FILE + the MAPPING to the server (the mapping is
// saved as the source's format profile, SI-16), the standard decision preview renders the
// byte-identical review (bands / pair table), and commit is the decisions-shaped payload.
// The old client-side `analyse()` preview brain stays deleted.
//
// SKIPPED 2026-07-27 (owner directive — match the handoff exactly): the Choose step's "Prefer
// to map the columns yourself?" link, this suite's ONLY way into the `configure` step, was
// removed (the prototype has no manual-mapping concept at all). The feature code itself
// (`runManualPreview`, `canManualPreview`, the configure step's FormFields) is intentionally
// left in place pending a separate decision — restore reachability some other way, or delete
// the feature outright. These tests are skipped rather than deleted so re-enabling either way
// is a one-line change, not a rewrite. See `PARITY_AND_GAPS.md` deviation #9 and
// `docs/adr/2026-07-27-statement-import-drop-account-auto-detect.md`'s follow-up note.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import * as helpers from './importHelpers'
import type { Account } from './types'

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

const CSV =
  'Date,Description,Amount,Category\n2026-06-05,Grab ride,-8.20,\n2026-06-07,LAZADA SG,-6.89,\n'

// The server's decision payload for the mapped file — a strong engine match on row 0 (the
// full pair-table anatomy must render for the MANUAL route too) and a clean row 1.
function serverDecisionPreview() {
  return {
    statementId: 'st-manual',
    drafts: [
      {
        date: '2026-06-05',
        payee: null,
        description: 'Grab ride',
        amount: -8.2,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 0,
        external_refs: [{ source: 'csv', ref: 'aaa111' }],
        flags: [
          {
            type: 'near_duplicate',
            reason: 'near_duplicate (score 93)',
            existing_id: 'e-1',
            existing: {
              note: 'Grab ride',
              amount: -8.2,
              currency: 'SGD',
              date: '2026-06-06',
              kind: 'expense',
            },
            band: 'strong',
          },
        ],
      },
      {
        date: '2026-06-07',
        payee: null,
        description: 'LAZADA SG',
        amount: -6.89,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 1,
        external_refs: [{ source: 'csv', ref: 'bbb222' }],
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
    formatProfile: { id: 'fp1', origin: 'manual', learned: true },
  }
}

async function openManualPreview() {
  const onPreview = vi.fn().mockResolvedValue(serverDecisionPreview())
  const onDecisionCommit = vi
    .fn()
    .mockResolvedValue({ created: 1, merged: 1, duplicates: 0, skipped: 0 })
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onDecisionCommit={onDecisionCommit as never}
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], 'bank.csv', { type: 'text/csv' })
  // jsdom's File lacks Blob#text() — the modal's pickFile awaits it for the mapping form grid.
  Object.defineProperty(file, 'text', { value: async () => CSV })
  fireEvent.change(fileInput, { target: { files: [file] } })
  // Choose step → take the manual mapping route.
  fireEvent.click(await screen.findByText(/Prefer to map the columns yourself/))
  // Configure step: the mapping auto-prefills from the headers; Preview sends it server-side.
  const previewBtn = await screen.findByRole('button', { name: 'Preview import' })
  await waitFor(() => expect((previewBtn as HTMLButtonElement).disabled).toBe(false))
  fireEvent.click(previewBtn)
  await waitFor(() => expect(onPreview).toHaveBeenCalled())
  return { onPreview, onDecisionCommit }
}

describe('ImportModal — manual mapping rides the server decision pipeline (SI-65)', () => {
  // Skipped — see the file header: `openManualPreview` navigates via the now-removed Choose
  // step link, so these three can no longer reach the configure step through the real UI.
  it.skip('Preview sends the FILE + the MAPPING to the server; no client-side parse books anything', async () => {
    const { onPreview } = await openManualPreview()
    const [file, url, accountId, mapping] = onPreview.mock.calls[0]!
    expect((file as File).name).toBe('bank.csv')
    expect(url).toBe('')
    expect(accountId).toBe('a1')
    // The taught mapping: real column NAMES from the file's header row.
    expect(mapping).toEqual({
      date: 'Date',
      description: 'Description',
      amount: 'Amount',
      category: 'Category',
    })
  })

  it.skip('the manual route renders the byte-identical decision review — strong band pair table included', async () => {
    await openManualPreview()
    // The full pair-row anatomy (bands/verdict) renders for the MANUAL route.
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    expect(document.querySelector('.imp-pair-row.strong')).toBeTruthy()
    expect(screen.getByText('LAZADA SG')).toBeTruthy()
  })

  it.skip('commit is the decisions-shaped payload against the cached statement', async () => {
    const { onDecisionCommit } = await openManualPreview()
    await waitFor(() => expect(document.querySelector('.imp-pair-row')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onDecisionCommit).toHaveBeenCalled())
    expect(onDecisionCommit.mock.calls[0]![0]).toBe('st-manual')
    expect(onDecisionCommit.mock.calls[0]![2]).toEqual([
      { row: 0, action: 'merge', existing_id: 'e-1' },
    ])
  })

  it('the deleted client preview brain stays deleted — importHelpers exports no analyse/structExtract', () => {
    expect((helpers as Record<string, unknown>).analyse).toBeUndefined()
    expect((helpers as Record<string, unknown>).structExtract).toBeUndefined()
  })
})
