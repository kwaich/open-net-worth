// @vitest-environment jsdom
//
// Attachments — add a file, rename one, write a note on one, remove one (after asking), the
// "Lucius can read" switch, and the empty state — against a mocked authFetch (the real api.ts parses every answer; RFC §3.9).
// Synthetic data only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AssetDoc, OwnedAsset } from '../../../types/assets'
import { Attachments } from './Attachments'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

const DOC: AssetDoc = {
  ref_id: 'ref_statement1',
  name: 'statement.pdf',
  added_on: '2026-09-01',
  lucius_readable: true,
}

function asset(docs: AssetDoc[]): OwnedAsset {
  return {
    id: 'oa_test000001',
    name: 'Test Condo',
    category: 'property',
    description: '',
    photo_ref: null,
    facts: [],
    notes: '',
    cost: null,
    cost_date: null,
    share_pct: 100,
    share_note: '',
    loan_account_id: null,
    insured: false,
    maturity_date: null,
    currency: 'SGD',
    value: 850000,
    valued_on: '2026-09-26',
    share_value: 850000,
    marks: [],
    docs,
    archived: false,
    disposed_on: null,
    disposal_proceeds: null,
    disposal_txn_id: null,
    disposal_account_id: null,
    created_at: '2026-09-26T00:00:00Z',
    updated_at: '2026-09-26T00:00:00Z',
  }
}

function ok(data: unknown): Response {
  return { ok: true, status: 200, json: async () => data } as Response
}

function refused(status: number, body: unknown): Response {
  return { ok: false, status, json: async () => body } as Response
}

function renderDocs(docs: AssetDoc[]) {
  const onToast = vi.fn()
  render(<Attachments a={asset(docs)} onToast={onToast} />)
  return { onToast }
}

const fileInput = () => document.querySelector('input[type="file"]') as HTMLInputElement

function openMenuItem(label: string, file = 'statement.pdf') {
  fireEvent.click(screen.getByRole('button', { name: `More actions for ${file}` }))
  fireEvent.click(screen.getByRole('menuitem', { name: label }))
}

function choose(file: File) {
  fireEvent.change(fileInput(), { target: { files: [file] } })
}

// A block body: an arrow returning the spy would hand Vitest a teardown that calls it.
beforeEach(() => {
  authFetch.mockReset()
})
afterEach(cleanup)

describe('Attachments — empty', () => {
  it('is the kit empty state, with Add file', () => {
    renderDocs([])
    expect(document.querySelector('.ui-state')).toBeTruthy()
    expect(screen.getByText('No documents yet')).toBeTruthy()
    expect(document.querySelector('.as-tbl-wrap .ui-state')).toBeTruthy()
    expect(
      screen.getByText(
        'Keep its documents here — the purchase agreement, title deed, receipts and photos.',
      ),
    ).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/based on/i)
    expect(screen.getByRole('button', { name: 'Add file' })).toBeTruthy()
    expect(document.body.textContent).not.toMatch(/\bmark\b/i)
  })
})

describe('Attachments — add a file', () => {
  it('opens the picker for supported types, uploads the file, then says File added.', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([DOC])))
    const { onToast } = renderDocs([])
    expect(fileInput().accept).toBe('image/*,.pdf,.csv,.xls,.xlsx,.ofx,.qfx,.qif')
    const picker = vi.spyOn(fileInput(), 'click')
    fireEvent.click(screen.getByRole('button', { name: 'Add file' }))
    expect(picker).toHaveBeenCalledTimes(1)

    const file = new File(['%PDF-1.7'], 'statement.pdf', { type: 'application/pdf' })
    choose(file)
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('File added.'))
    const [path, init] = authFetch.mock.calls[0]!
    expect(path).toBe('/api/assets/oa_test000001/docs')
    expect((init as RequestInit).method).toBe('POST')
    expect(((init as RequestInit).body as FormData).get('file')).toBe(file)
    // Choosing the same file again must still fire a change.
    expect(fileInput().value).toBe('')
  })

  it('says why a file was refused, and adds nothing', async () => {
    authFetch.mockResolvedValueOnce(
      refused(413, { detail: 'That file is over 25 MB.', code: 'too_large', field: 'file' }),
    )
    const { onToast } = renderDocs([DOC])
    choose(new File(['x'], 'scan.pdf', { type: 'application/pdf' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      "scan.pdf wasn't added — That file is over 25 MB.",
    )
    expect(onToast).not.toHaveBeenCalled()
  })

  it('takes several files at once, and a refused one never stops the rest', async () => {
    authFetch
      .mockResolvedValueOnce(ok(asset([DOC])))
      .mockResolvedValueOnce(
        refused(415, {
          detail: "That file type can't be read.",
          code: 'unsupported',
          field: 'file',
        }),
      )
      .mockResolvedValueOnce(ok(asset([DOC])))
    const { onToast } = renderDocs([])
    expect(fileInput().multiple).toBe(true)
    const files = ['spa.pdf', 'notes.exe', 'receipt.jpg'].map((n) => new File(['x'], n))
    fireEvent.change(fileInput(), { target: { files } })
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('2 files added.'))
    expect(authFetch).toHaveBeenCalledTimes(3)
    expect(screen.getByRole('alert').textContent).toBe(
      "notes.exe wasn't added — That file type can't be read.",
    )
  })
})

describe('Attachments — rename', () => {
  it('renames a file from its ⋯ menu, tidying spaces, then says File renamed.', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([{ ...DOC, name: 'SPA page 1' }])))
    const { onToast } = renderDocs([DOC])
    openMenuItem('Rename')
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Rename file')
    const field = screen.getByRole('textbox', { name: /Name/ }) as HTMLInputElement
    expect(field.value).toBe('statement.pdf')
    fireEvent.change(field, { target: { value: '  SPA   page 1 ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('File renamed.'))
    const [path, init] = authFetch.mock.calls[0]!
    expect(path).toBe('/api/assets/oa_test000001/docs/ref_statement1')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      changes: { name: 'SPA page 1' },
    })
  })

  it('refuses a blank name beside the field and sends nothing', () => {
    renderDocs([DOC])
    openMenuItem('Rename')
    fireEvent.change(screen.getByRole('textbox', { name: /Name/ }), { target: { value: '   ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByText('Enter a name.')).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalled()
  })

  it('selects the whole name — a label, so typing replaces it (the file keeps its own name)', () => {
    renderDocs([{ ...DOC, name: 'df25fa9c6f98 2.JPEG' }])
    openMenuItem('Rename', 'df25fa9c6f98 2.JPEG')
    const field = screen.getByRole('textbox', { name: /Name/ }) as HTMLInputElement
    fireEvent.focus(field)
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, 'df25fa9c6f98 2.JPEG'.length])
  })
})

describe('Attachments — a note (the one Documents shows)', () => {
  const sent = () => JSON.parse(String((authFetch.mock.calls[0]![1] as RequestInit).body))

  it('writes one from the ⋯ menu, trimmed, then says Note saved.', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([{ ...DOC, note: 'Signed copy' }])))
    const { onToast } = renderDocs([DOC])
    openMenuItem('Add a note')
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Add a note')
    expect(screen.getByText(/Lucius reads your note/)).toBeTruthy()
    fireEvent.change(screen.getByRole('textbox', { name: /Note/ }), {
      target: { value: '  Signed copy \n' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('Note saved.'))
    expect(sent()).toEqual({ changes: { note: 'Signed copy' } })
  })

  it('shows under the name; Edit note opens it and Remove note deletes it', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([DOC])))
    const { onToast } = renderDocs([{ ...DOC, note: 'Signed copy', lucius_readable: false }])
    expect(document.querySelector('.as-doc-note')?.textContent).toBe('Signed copy')
    openMenuItem('Edit note')
    const field = screen.getByRole('textbox', { name: /Note/ }) as HTMLTextAreaElement
    expect(field.value).toBe('Signed copy')
    expect(screen.getByText(/can still read this note/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove note' }))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('Note removed.'))
    expect(sent()).toEqual({ changes: { note: null } })
  })
})

describe('Attachments — the list', () => {
  it('lists each file with when it was added, its Lucius switch and a ⋯ menu', () => {
    renderDocs([DOC])
    expect(screen.getByText('statement.pdf')).toBeTruthy()
    expect(screen.getByText('Sep 2026')).toBeTruthy()
    expect((screen.getByRole('switch') as HTMLInputElement).checked).toBe(true)
    // One ⋯ per row holds Rename, the note and Remove — no lone trash icon beside it.
    expect(screen.queryByRole('button', { name: 'Remove statement.pdf' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'More actions for statement.pdf' }))
    const items = screen.getAllByRole('menuitem').map((el) => el.textContent)
    expect(items).toEqual(['Rename', 'Add a note', 'Remove'])
    expect(screen.getByRole('button', { name: 'Add file' })).toBeTruthy()
  })

  it('puts Add file under the files, where the next one goes (owner 2026-09-28)', () => {
    renderDocs([DOC])
    const add = screen.getByRole('button', { name: 'Add file' })
    const list = document.querySelector('.as-tbl-wrap') as HTMLElement
    expect(list.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('removes a file only after asking, then says File removed.', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([])))
    const { onToast } = renderDocs([DOC])
    openMenuItem('Remove')
    expect(screen.getByText('Remove this file?')).toBeTruthy()
    expect(screen.getByText('statement.pdf will be removed from this asset.')).toBeTruthy()
    expect(authFetch).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('File removed.'))
    expect(authFetch).toHaveBeenCalledWith('/api/assets/oa_test000001/docs/ref_statement1', {
      method: 'DELETE',
    })
    expect(screen.queryByText('Remove this file?')).toBeNull()
  })

  it('a refused removal stays in the confirm with the reason', async () => {
    authFetch.mockResolvedValueOnce(
      refused(404, { detail: 'That asset (oa_test000001) no longer exists.', code: 'not_found' }),
    )
    const { onToast } = renderDocs([DOC])
    openMenuItem('Remove')
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect((await screen.findByRole('alert')).textContent).toBe(
      'That asset (oa_test000001) no longer exists.',
    )
    expect(screen.getByText('Remove this file?')).toBeTruthy()
    expect(onToast).not.toHaveBeenCalled()
  })

  it('saves the Lucius switch, and says so when that fails', async () => {
    authFetch
      .mockResolvedValueOnce(ok(asset([{ ...DOC, lucius_readable: false }])))
      .mockResolvedValueOnce(refused(503, { detail: 'The server is busy. Try again.' }))
    const { onToast } = renderDocs([DOC])
    fireEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(1))
    const [path, init] = authFetch.mock.calls[0]!
    expect(path).toBe('/api/assets/oa_test000001/docs/ref_statement1')
    expect(JSON.parse(String((init as RequestInit).body))).toEqual({
      changes: { lucius_readable: false },
    })
    fireEvent.click(screen.getByRole('switch'))
    await waitFor(() => expect(onToast).toHaveBeenCalledWith('The server is busy. Try again.'))
  })
})

describe('Attachments — opening a file', () => {
  const PHOTO: AssetDoc = {
    ref_id: 'att_photo00001',
    name: 'Payment Schedule',
    added_on: '2026-09-02',
    lucius_readable: true,
    filename: 'IMG_1234.JPEG',
    media_type: 'image/jpeg',
    size_bytes: 2_400_000,
  }

  it('shows an image as a thumbnail in its row', () => {
    renderDocs([PHOTO, DOC])
    const thumbs = document.querySelectorAll('img.as-doc-thumb')
    expect(thumbs).toHaveLength(1)
    expect(thumbs[0]!.getAttribute('src')).toBe('/api/uploads/att_photo00001/file')
  })

  it('opens a tapped row as a preview — an image as itself, named and sized', () => {
    renderDocs([PHOTO])
    fireEvent.click(screen.getByText('Added').closest('table')!.querySelector('tbody tr')!)
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('Payment Schedule')
    expect(document.querySelector('.ui-modal__subtitle')?.textContent).toBe(
      'IMG_1234.JPEG · 2.3 MB · Added Sep 2026',
    )
    const img = document.querySelector('img.as-viewer-img')
    expect(img?.getAttribute('src')).toBe('/api/uploads/att_photo00001/file')
    expect(screen.getByRole('button', { name: 'Download' })).toBeTruthy()
  })

  it('never opens it from the Lucius switch or the ⋯ menu', () => {
    authFetch.mockResolvedValue(ok(asset([PHOTO])))
    renderDocs([PHOTO])
    fireEvent.click(screen.getByRole('switch'))
    fireEvent.click(screen.getByRole('button', { name: 'More actions for Payment Schedule' }))
    expect(document.querySelector('img.as-viewer-img')).toBeNull()
  })

  it('says so when a file has no preview, and still offers Download', () => {
    renderDocs([{ ...DOC, media_type: 'application/octet-stream', filename: 'backup.qif' }])
    fireEvent.click(screen.getByRole('button', { name: 'statement.pdf' }))
    expect(screen.getByText('No preview for this file')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Download' })).toBeTruthy()
  })
})

describe('Attachments — on a phone', () => {
  const phone = ((query: string) => ({
    matches: /max-width/.test(query),
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
  let original: typeof window.matchMedia
  beforeEach(() => {
    original = window.matchMedia
    Object.defineProperty(window, 'matchMedia', {
      value: phone,
      configurable: true,
      writable: true,
    })
  })
  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      value: original,
      configurable: true,
      writable: true,
    })
  })

  it('lists each file as one row — no table to scroll sideways to reach its controls', () => {
    renderDocs([DOC])
    expect(document.querySelector('.as-tbl')).toBeNull()
    expect(screen.getByText('Sep 2026 · Lucius can read')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'More actions for statement.pdf' }))
    expect(screen.getAllByRole('menuitem').map((el) => el.textContent)).toEqual([
      'Rename',
      'Add a note',
      'Don’t let Lucius read it',
      'Remove',
    ])
  })

  it('switches Lucius off from the row menu, and opens the file from the row', async () => {
    authFetch.mockResolvedValueOnce(ok(asset([{ ...DOC, lucius_readable: false }])))
    renderDocs([DOC])
    openMenuItem('Don’t let Lucius read it')
    await waitFor(() => expect(authFetch).toHaveBeenCalledTimes(1))
    expect(JSON.parse(String((authFetch.mock.calls[0]![1] as RequestInit).body))).toEqual({
      changes: { lucius_readable: false },
    })
    fireEvent.click(screen.getByRole('button', { name: /^statement\.pdf/ }))
    expect(document.querySelector('.ui-modal__title')?.textContent).toBe('statement.pdf')
  })
})
