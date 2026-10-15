// @vitest-environment jsdom
//
// First-run restore. The path this exists for: someone who already uses Open Net Worth on
// another machine, has an export or a backup archive, and should NOT have to answer a single
// setup question before getting their book back. Before this, restore lived only in
// Settings › Data — reachable after choosing a data folder, a model and a password, then
// landing on an empty dashboard and going hunting.
//
// It is a preview-then-commit flow over endpoints that already ship: /api/data/import/validate
// writes nothing and returns an ImportReport; /api/data/import with replace=true does the write,
// wrapped in a pre-import checkpoint.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react'
import { StepRestore } from './StepRestore'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../api/client', () => ({ authFetch }))

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

const GOOD_REPORT = {
  ok: true,
  schema_version: 3,
  base_currency: 'SGD',
  files: [
    { name: 'transactions.json', row_count: 4120, sha_ok: true },
    { name: 'accounts.json', row_count: 11, sha_ok: true },
    { name: 'positions.json', row_count: 24, sha_ok: true },
  ],
  warnings: [],
  errors: [],
}

function mockApi(report: unknown, importOk = true) {
  authFetch.mockImplementation((url: string) => {
    if (url === '/api/data/import/validate') {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(report) })
    }
    if (url === '/api/data/import') {
      return importOk
        ? Promise.resolve({ ok: true, json: () => Promise.resolve({ undo_checkpoint_id: 'ck_1' }) })
        : Promise.resolve({ ok: false, json: () => Promise.resolve({ detail: 'bundle rejected' }) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

function pickFile(name = 'open-net-worth-export.zip') {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement
  const file = new File(['zip-bytes'], name, { type: 'application/zip' })
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  fireEvent.change(input)
  return file
}

function bodyOf(url: string) {
  const call = authFetch.mock.calls.find((c) => c[0] === url)
  return call?.[1]?.body as FormData | undefined
}

describe('StepRestore — preview before it writes anything', () => {
  it('locks the selected file while its preview is pending so commit uses the previewed bundle', async () => {
    let finish!: (response: { ok: boolean; json: () => Promise<typeof GOOD_REPORT> }) => void
    mockApi(GOOD_REPORT)
    authFetch.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve
        }),
    )
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)
    const selected = pickFile('first.zip')
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input.disabled).toBe(true)
    pickFile('replacement.zip')
    await act(async () => finish({ ok: true, json: async () => GOOD_REPORT }))
    expect(screen.getByText('first.zip')).toBeTruthy()
    expect(screen.queryByText('replacement.zip')).toBeNull()
    expect(input.disabled).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: /restore this/i }))
    await waitFor(() => expect(bodyOf('/api/data/import')?.get('file')).toBe(selected))
  })

  it('removes the preview and restore action when the selected file is cleared', async () => {
    mockApi(GOOD_REPORT)
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)
    pickFile()
    await screen.findByRole('button', { name: /restore this/i })
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [], configurable: true })
    fireEvent.change(input)
    expect(screen.queryByRole('button', { name: /restore this/i })).toBeNull()
    expect(screen.queryByText(/4,120/)).toBeNull()
  })

  it('clears a failed preview error when the selected file is removed', async () => {
    authFetch.mockResolvedValue({ ok: false, json: async () => ({ detail: 'broken export' }) })
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)
    pickFile()
    await screen.findByText('broken export')
    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    Object.defineProperty(input, 'files', { value: [], configurable: true })
    fireEvent.change(input)
    expect(screen.queryByText('broken export')).toBeNull()
  })

  it('validates the chosen bundle without importing it', async () => {
    mockApi(GOOD_REPORT)
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)

    pickFile()

    await waitFor(() => expect(bodyOf('/api/data/import/validate')).toBeTruthy())
    // The commit endpoint must NOT have been touched by merely choosing a file.
    expect(authFetch.mock.calls.some((c) => c[0] === '/api/data/import')).toBe(false)
  })

  it('shows what is actually in the bundle', async () => {
    mockApi(GOOD_REPORT)
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)

    pickFile()

    expect(await screen.findByText(/SGD/)).toBeTruthy()
    expect(screen.getByText(/4,120/)).toBeTruthy()
    expect(screen.getByText(/transactions/i)).toBeTruthy()
  })

  it('leaves the currency row out when the bundle states none', async () => {
    mockApi({ ...GOOD_REPORT, base_currency: '' })
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)

    pickFile()

    expect(await screen.findByText(/4,120/)).toBeTruthy()
    expect(screen.queryByText(/app currency/i)).toBeNull()
    expect(screen.queryByText(/not stated/i)).toBeNull()
  })

  it('refuses to offer the import when the bundle is invalid', async () => {
    const message =
      "This file isn't an Open Net Worth backup. Choose the .zip you exported from Settings \u2192 Data."
    mockApi({ ...GOOD_REPORT, ok: false, errors: [message] })
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)

    pickFile('notes.txt')

    expect(await screen.findByText(message)).toBeTruthy()
    expect(screen.queryByRole('button', { name: /restore this/i })).toBeNull()
  })

  it('surfaces warnings without blocking the import', async () => {
    mockApi({ ...GOOD_REPORT, warnings: ['statements/ was skipped'] })
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)

    pickFile()

    expect(await screen.findByText(/statements\/ was skipped/i)).toBeTruthy()
    expect(screen.getByRole('button', { name: /restore this/i })).toBeTruthy()
  })
})

describe('StepRestore — the commit', () => {
  it('imports with replace=true only after an explicit confirm', async () => {
    mockApi(GOOD_REPORT)
    render(<StepRestore onDone={() => {}} onBack={() => {}} />)

    pickFile()
    fireEvent.click(await screen.findByRole('button', { name: /restore this/i }))

    await waitFor(() => {
      const body = bodyOf('/api/data/import')
      expect(body).toBeTruthy()
      expect(body!.get('replace')).toBe('true')
    })
  })

  it('hands restored data back to setup without marking machine setup complete', async () => {
    mockApi(GOOD_REPORT)
    const onDone = vi.fn()
    render(<StepRestore onDone={onDone} onBack={() => {}} />)

    pickFile()
    fireEvent.click(await screen.findByRole('button', { name: /restore this/i }))

    await waitFor(() => expect(onDone).toHaveBeenCalled())
    expect(authFetch.mock.calls.some((c) => c[0] === '/api/setup-status/complete')).toBe(false)
  })

  it('reports a failed import instead of pretending it worked', async () => {
    mockApi(GOOD_REPORT, false)
    const onDone = vi.fn()
    render(<StepRestore onDone={onDone} onBack={() => {}} />)

    pickFile()
    fireEvent.click(await screen.findByRole('button', { name: /restore this/i }))

    expect(await screen.findByText(/bundle rejected/i)).toBeTruthy()
    expect(onDone).not.toHaveBeenCalled()
  })

  it('lets the user back out to normal setup', async () => {
    mockApi(GOOD_REPORT)
    const onBack = vi.fn()
    render(<StepRestore onDone={() => {}} onBack={onBack} />)

    fireEvent.click(screen.getByRole('button', { name: /set up instead/i }))
    expect(onBack).toHaveBeenCalled()
  })
})
