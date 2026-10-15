// @vitest-environment jsdom
//
// SI-67 — upload-time instructions: the choose step's optional textarea rides the preview
// request (remembered per source server-side — "Remember for this source" checked by
// default; unchecked = one-off, this import only). The review screen's own "N instruction(s)
// applied · edit" meta line was removed — instructions are still taught (this file) and still
// re-applied on the saved format profile,
// there is just no longer an inline view/edit affordance on the review step itself. The
// AI-cost fact carries a "Re-read" link that forces a fresh billed parse.

import { describe, it, expect, afterEach, vi } from 'vitest'
import { render, cleanup, screen, fireEvent, waitFor } from '@testing-library/react'
import { ImportModal } from './ImportModal'
import type { Account } from './types'

const authFetch = vi.fn(async () => ({
  ok: true,
  json: async () => ({ saved: true, text: 'Skip HOLD rows.' }),
}))
vi.mock('../../../api/client', () => ({ authFetch: (...a: unknown[]) => authFetch(...(a as [])) }))

afterEach(() => {
  cleanup()
  authFetch.mockClear()
})

const acct = {
  id: 'a1',
  name: 'Mega Bank',
  kind: 'asset',
  type: 'cash',
  currency: 'SGD',
} as unknown as Account

const CSV = 'Date,Description,Amount\n2026-07-01,COFFEE,-12.50\n'

function previewPayload(
  instructions: {
    text: string
    lines: number
    used_in_parse: boolean
    remembered?: boolean
  } | null,
) {
  return {
    statementId: 'st-1',
    drafts: [
      {
        date: '2026-07-01',
        payee: null,
        description: 'COFFEE',
        amount: -12.5,
        currency: 'SGD',
        kind: 'expense',
        category: null,
        dup: false,
        rowIndex: 0,
        external_refs: [{ source: 'csv', ref: 'c1' }],
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
    instructions,
  }
}

async function toChooseStep(
  onPreview: ReturnType<typeof vi.fn>,
  onSourcePeek?: ReturnType<typeof vi.fn>,
) {
  render(
    <ImportModal
      open
      accounts={[acct]}
      defaultAccountId="a1"
      onClose={() => {}}
      onPreview={onPreview as never}
      onSourcePeek={onSourcePeek as never}
      onDecisionCommit={
        vi.fn().mockResolvedValue({ created: 1, merged: 0, duplicates: 0, skipped: 0 }) as never
      }
    />,
  )
  const fileInput = document.body.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File([CSV], 'mega.csv', { type: 'text/csv' })
  Object.defineProperty(file, 'text', { value: async () => CSV })
  fireEvent.change(fileInput, { target: { files: [file] } })
  await screen.findByRole('button', { name: /Read the statement/ })
}

describe('ImportModal — upload-time instructions (SI-67)', () => {
  it('the choose step renders the optional textarea and submits its text with the preview', async () => {
    const onPreview = vi.fn().mockResolvedValue(
      previewPayload({
        text: 'Skip HOLD rows.\nFees are separate.',
        lines: 2,
        used_in_parse: true,
      }),
    )
    await toChooseStep(onPreview)
    const box = screen.getByLabelText('Tell Lucius') as HTMLTextAreaElement
    expect(box.maxLength).toBe(1000)
    fireEvent.change(box, { target: { value: 'Skip HOLD rows.\nFees are separate.' } })
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    // (file, url, accountId, mapping, instructions)
    expect(onPreview.mock.calls[0]![4]).toBe('Skip HOLD rows.\nFees are separate.')
  })

  it('blank textarea sends NO instructions field', async () => {
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    await toChooseStep(onPreview)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![4]).toBeUndefined()
    await screen.findByText('COFFEE')
    expect(screen.queryByText(/instruction/)).toBeNull()
  })

  it('the Remember checkbox renders CHECKED by default and the preview submits remember=true', async () => {
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    await toChooseStep(onPreview)
    const box = screen.getByRole('checkbox', {
      name: /Remember for this source/,
    }) as HTMLInputElement
    expect(box.checked).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    // (file, url, accountId, mapping, instructions, remember, forceReparse)
    expect(onPreview.mock.calls[0]![5]).toBe(true)
  })

  it('unchecking Remember sends remember=false with the preview (one-off)', async () => {
    const onPreview = vi.fn().mockResolvedValue(
      previewPayload({
        text: 'Skip HOLD rows.',
        lines: 1,
        used_in_parse: true,
        remembered: false,
      }),
    )
    await toChooseStep(onPreview)
    fireEvent.change(screen.getByLabelText('Tell Lucius'), { target: { value: 'Skip HOLD rows.' } })
    fireEvent.click(screen.getByRole('checkbox', { name: /Remember for this source/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![4]).toBe('Skip HOLD rows.')
    expect(onPreview.mock.calls[0]![5]).toBe(false)
  })

  it('the AI-cost fact carries a Re-read link that re-runs the preview with the force flag', async () => {
    const onPreview = vi.fn().mockResolvedValue({ ...previewPayload(null), llmCostUsd: 0 })
    await toChooseStep(onPreview)
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await screen.findByText(/read from memory/)
    fireEvent.click(screen.getByRole('button', { name: 'Re-read' }))
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(2))
    expect(onPreview.mock.calls[1]![6]).toBe(true)
    expect(onPreview.mock.calls[0]![6]).toBeFalsy()
  })

  it('the known-source banner\'s "Re-read from scratch" makes the FIRST call skip the cache AND re-detect', async () => {
    // Regression (owner-reported live incident, 2026-07-19): re-uploading the exact same file
    // silently replayed a stale cached parse (StatementStore dedupes by content hash) — a
    // fixed bug never showed up without knowing to click "Re-read" after the fact. This
    // checkbox lets the caller ask for freshness UP FRONT, on the very first call.
    // Ported to the handoff's ONE control: it drives force_reparse AND force_relearn together
    // ("fresh format detection + fresh processing", flow3.jsx), and only a KNOWN source has
    // anything remembered to throw away, so it only appears with the banner.
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    const onSourcePeek = vi
      .fn()
      .mockResolvedValue({ known: true, instructions: '', formatLabel: 'Mega CSV' })
    await toChooseStep(onPreview, onSourcePeek)
    await screen.findByText(/I know this source/)
    fireEvent.click(screen.getByRole('checkbox', { name: /Re-read from scratch/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalledTimes(1))
    // (file, url, accountId, mapping, instructions, remember, forceReparse, forceRelearn)
    expect(onPreview.mock.calls[0]![6]).toBe(true)
    expect(onPreview.mock.calls[0]![7]).toBe(true)
  })

  it('a FAILED peek still offers the re-read control (the only force_relearn escape)', async () => {
    // The probe is best-effort, but it gates the ONLY control that sets force_relearn. A
    // network hiccup must not take the escape from a stuck format profile with it: we cannot
    // claim "I know this source", but we can still offer to forget what might be remembered.
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    const onSourcePeek = vi.fn().mockRejectedValue(new Error('network down'))
    await toChooseStep(onPreview, onSourcePeek)
    await waitFor(() => expect(onSourcePeek).toHaveBeenCalled())
    expect(screen.queryByText(/I know this source/)).toBeNull()
    fireEvent.click(await screen.findByRole('checkbox', { name: /Re-read from scratch/ }))
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![6]).toBe(true)
    expect(onPreview.mock.calls[0]![7]).toBe(true)
  })

  it('an UNKNOWN source shows neither the banner nor its re-read control', async () => {
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    const onSourcePeek = vi
      .fn()
      .mockResolvedValue({ known: false, instructions: '', formatLabel: null })
    await toChooseStep(onPreview, onSourcePeek)
    await waitFor(() => expect(onSourcePeek).toHaveBeenCalled())
    expect(screen.queryByText(/I know this source/)).toBeNull()
    expect(screen.queryByRole('checkbox', { name: /Re-read from scratch/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![6]).toBeFalsy()
    expect(onPreview.mock.calls[0]![7]).toBeFalsy()
  })

  it('picking a known file pre-fills the saved instructions (source peek)', async () => {
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    const onSourcePeek = vi.fn().mockResolvedValue({
      known: true,
      instructions: 'Skip HOLD rows.',
      formatLabel: 'STT No. • Ngày/TNX Date',
    })
    await toChooseStep(onPreview, onSourcePeek)
    expect(onSourcePeek).toHaveBeenCalledTimes(1)
    const box = screen.getByLabelText('Tell Lucius') as HTMLTextAreaElement
    await waitFor(() => expect(box.value).toBe('Skip HOLD rows.'))
  })

  it('an untouched prefill submits with the preview; an EDIT round-trips the edited text', async () => {
    const onPreview = vi.fn().mockResolvedValue(
      previewPayload({
        text: 'Skip HOLD rows. And skip FX fees.',
        lines: 1,
        used_in_parse: true,
      }),
    )
    const onSourcePeek = vi.fn().mockResolvedValue({ known: true, instructions: 'Skip HOLD rows.' })
    await toChooseStep(onPreview, onSourcePeek)
    const box = screen.getByLabelText('Tell Lucius') as HTMLTextAreaElement
    await waitFor(() => expect(box.value).toBe('Skip HOLD rows.'))
    fireEvent.change(box, { target: { value: 'Skip HOLD rows. And skip FX fees.' } })
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    // (file, url, accountId, mapping, instructions, remember) — edited text, remember default TRUE.
    expect(onPreview.mock.calls[0]![4]).toBe('Skip HOLD rows. And skip FX fees.')
    expect(onPreview.mock.calls[0]![5]).toBe(true)
  })

  it('a failed or unknown peek leaves the box empty and never blocks the choose step', async () => {
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    const onSourcePeek = vi.fn().mockRejectedValue(new Error('network down'))
    await toChooseStep(onPreview, onSourcePeek)
    const box = screen.getByLabelText('Tell Lucius') as HTMLTextAreaElement
    await waitFor(() => expect(onSourcePeek).toHaveBeenCalled())
    expect(box.value).toBe('')
    fireEvent.click(screen.getByRole('button', { name: /Read the statement/ }))
    await waitFor(() => expect(onPreview).toHaveBeenCalled())
    expect(onPreview.mock.calls[0]![4]).toBeUndefined()
  })

  it('a slow peek reply never clobbers text the user typed meanwhile', async () => {
    const onPreview = vi.fn().mockResolvedValue(previewPayload(null))
    let resolvePeek: (v: { known: boolean; instructions: string }) => void = () => {}
    const onSourcePeek = vi.fn().mockReturnValue(
      new Promise((res) => {
        resolvePeek = res
      }),
    )
    await toChooseStep(onPreview, onSourcePeek)
    const box = screen.getByLabelText('Tell Lucius') as HTMLTextAreaElement
    fireEvent.change(box, { target: { value: 'My own note.' } })
    resolvePeek({ known: true, instructions: 'Saved note.' })
    await waitFor(() => expect(onSourcePeek).toHaveBeenCalled())
    expect(box.value).toBe('My own note.')
  })
})
