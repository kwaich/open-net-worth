// @vitest-environment jsdom
//
// B20 async — reopening a statement by id (`resumeStatementId`): `ready` goes straight to
// review via POST /api/statements/{id}/preview (no re-upload); `parsing` shows the same
// processing UI, polled by id, and auto-transitions to review once the poll reports `ready`.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const ACCOUNTS = [
  {
    id: 'rvb',
    name: 'Riverside Bank',
    currency: 'USD',
    color: '#3b86dd',
    kind: 'asset',
    type: 'cash',
    archived: false,
  },
] as unknown as Account[]

function jsonResponse(body: unknown, ok = true) {
  return Promise.resolve({ ok, json: async () => body })
}

const PREVIEW_RESULT = {
  statementId: 'stmt_resume_1',
  drafts: [
    {
      date: '2026-07-01',
      payee: null,
      description: 'COFFEE',
      amount: -12.5,
      currency: 'USD',
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
}

function open(resumeStatementId: string, onReopenPreview: ReturnType<typeof vi.fn>) {
  return render(
    <ImportModal
      open
      accounts={ACCOUNTS}
      resumeStatementId={resumeStatementId}
      resumeFilename="jan.pdf"
      onReopenPreview={onReopenPreview as never}
      onClose={() => {}}
      onPreview={vi.fn() as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
    />,
  )
}

describe('ImportModal — resume by id (B20 async)', () => {
  it('opens straight into processing, then jumps to review once ready', async () => {
    authFetch.mockImplementation((url: string) =>
      url.includes('/parse-status')
        ? jsonResponse({ parse_status: 'ready', progress: null, error: null })
        : jsonResponse({}),
    )
    const onReopenPreview = vi.fn().mockResolvedValue(PREVIEW_RESULT)
    open('stmt_resume_1', onReopenPreview)

    expect(document.body.querySelector('.imp3-reading')).toBeTruthy()
    await waitFor(() => expect(onReopenPreview).toHaveBeenCalledWith('stmt_resume_1', undefined))
    await screen.findByText('COFFEE')
  })

  it('a still-parsing id polls by statement id and auto-transitions once ready', async () => {
    let calls = 0
    authFetch.mockImplementation((url: string) => {
      if (!url.includes('/parse-status')) return jsonResponse({})
      calls += 1
      if (calls === 1) {
        return jsonResponse({
          parse_status: 'parsing',
          progress: {
            stages: [
              {
                stage: 'read',
                label: 'Reading page 1 of 3',
                started_at: new Date().toISOString(),
                done_at: null,
                page: 1,
                pages: 3,
                retry: null,
              },
            ],
            started_at: new Date().toISOString(),
            elapsed_s: 5,
            done: false,
            error: null,
            statement_id: 'stmt_resume_2',
          },
          error: null,
        })
      }
      return jsonResponse({ parse_status: 'ready', progress: null, error: null })
    })
    const onReopenPreview = vi
      .fn()
      .mockResolvedValue({ ...PREVIEW_RESULT, statementId: 'stmt_resume_2' })
    open('stmt_resume_2', onReopenPreview)

    // A still-parsing id opens straight into the processing step...
    expect(document.body.querySelector('.imp3-reading')).toBeTruthy()
    // ...and auto-transitions to review the moment a poll (by statement id) reports ready —
    // the two mocked polls resolve near-instantly here, so the intermediate `parsing` phase
    // is real but not reliably observable mid-test; the auto-transition itself is the thing
    // this test pins.
    await waitFor(() => expect(onReopenPreview).toHaveBeenCalledWith('stmt_resume_2', undefined), {
      timeout: 3000,
    })
    await screen.findByText('COFFEE')
    expect(calls).toBeGreaterThanOrEqual(2) // both the one-shot check and at least one poll ran
  })

  it('a resumed statement never shows the old "Stop reading?" confirm on close', async () => {
    authFetch.mockImplementation(() =>
      jsonResponse({ parse_status: 'parsing', progress: null, error: null }),
    )
    open('stmt_resume_3', vi.fn())
    await waitFor(() => expect(document.body.querySelector('.imp3-reading')).toBeTruthy())
    expect(screen.queryByText('Stop reading this statement?')).toBeNull()
  })
})

describe('ImportModal — a sync upload attaches to an already-parsing async job (code review round B, M4)', () => {
  it('switches into the resume-by-id flow on a 202 attach response, never renders it as an empty preview', async () => {
    authFetch.mockImplementation((url: string) =>
      url.includes('/parse-status')
        ? jsonResponse({ parse_status: 'ready', progress: null, error: null })
        : jsonResponse({}),
    )
    // The plain sync preview call comes back ATTACHED — someone else's upload of the exact
    // same bytes is already `parsing` server-side — never a real (empty) preview shape.
    const onPreview = vi.fn().mockResolvedValue({ attached: true, statementId: 'stmt_attach_1' })
    const onReopenPreview = vi.fn().mockResolvedValue(PREVIEW_RESULT)
    render(
      <ImportModal
        open
        accounts={ACCOUNTS}
        defaultAccountId="rvb"
        onClose={() => {}}
        onPreview={onPreview as never}
        onReopenPreview={onReopenPreview as never}
        onDecisionCommit={
          vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
        }
      />,
    )
    const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['%PDF-1.4 fake'], 'statement.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })
    const luBtn = await screen.findByText(/Read the statement/i)
    fireEvent.click(luBtn)

    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    // Never a "0 to import" empty preview render — it moved straight to the resume flow.
    expect(screen.queryByText(/to import/i)).toBeNull()
    await waitFor(() =>
      expect(onReopenPreview).toHaveBeenCalledWith('stmt_attach_1', 'rvb' as unknown),
    )
    await screen.findByText('COFFEE')
  })
})
