// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { VaultItem } from '../../api/vault'
import { ManageCard, NoteCard } from './VaultDetailSections'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
const refreshVault = vi.fn()
vi.mock('../../hooks/useVault', () => ({ refreshVault: () => refreshVault() }))
const setVaultNote = vi.fn<(id: string, note: string | null) => Promise<VaultItem>>()
vi.mock('../../api/vault', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/vault')>()
  return { ...actual, setVaultNote: (id: string, note: string | null) => setVaultNote(id, note) }
})

function item(over: Partial<VaultItem> = {}): VaultItem {
  return {
    id: 'statement-stmt_1',
    kind: 'statement',
    source_id: 'stmt_1',
    title: 'Everyday — Aug 2026',
    filename: 'aug.pdf',
    media_type: 'application/pdf',
    size_bytes: 2048,
    added_at: '2026-09-20T10:00:00Z',
    year: 2026,
    private: false,
    file_url: '/api/statements/stmt_1/file',
    note: null,
    ...over,
  }
}

beforeEach(() => {
  refreshVault.mockReset()
  setVaultNote.mockReset().mockImplementation(async (_id, note) => item({ note }))
})
afterEach(cleanup)

describe('a note on a document', () => {
  it('shows as typed — line breaks kept', () => {
    render(<NoteCard note={'Signed copy\nOriginal in the safe'} />)
    expect(screen.getByText('Note')).toBeTruthy()
    expect(document.querySelector('.vault-note')?.textContent).toBe(
      'Signed copy\nOriginal in the safe',
    )
  })

  it('is added from Manage on any kind, trimmed, and the list refreshes', async () => {
    render(<ManageCard item={item()} onRemoved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a note' }))
    const dialog = await screen.findByRole('dialog', { name: 'Add a note' })
    expect(within(dialog).queryByRole('button', { name: 'Remove' })).toBeNull()
    expect(within(dialog).getByText(/Lucius reads your note/)).toBeTruthy()
    fireEvent.change(within(dialog).getByLabelText('Note'), {
      target: { value: '  Paid from the joint account \n' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(setVaultNote).toHaveBeenCalledWith('statement-stmt_1', 'Paid from the joint account')
    expect(refreshVault).toHaveBeenCalled()
  })

  it('is edited in place, and Remove clears it', async () => {
    render(<ManageCard item={item({ note: 'Old note' })} onRemoved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit note' }))
    const dialog = await screen.findByRole('dialog', { name: 'Edit note' })
    expect((within(dialog).getByLabelText('Note') as HTMLTextAreaElement).value).toBe('Old note')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(setVaultNote).toHaveBeenCalledWith('statement-stmt_1', null))
  })

  it('saving it unchanged writes nothing', async () => {
    render(<ManageCard item={item({ note: 'Same' })} onRemoved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Edit note' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(setVaultNote).not.toHaveBeenCalled()
  })

  it('on a Private file it says Lucius can still read the note', async () => {
    render(<ManageCard item={item({ kind: 'file', private: true })} onRemoved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a note' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Private hides only what’s in the file/)).toBeTruthy()
  })

  it('a refused save says why and keeps what was typed', async () => {
    setVaultNote.mockRejectedValue(new Error('A note can be up to 1000 characters.'))
    render(<ManageCard item={item()} onRemoved={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: 'Add a note' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByLabelText('Note'), { target: { value: 'Too long' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(dialog).findByText(/up to 1000 characters/)).toBeTruthy()
    expect((within(dialog).getByLabelText('Note') as HTMLTextAreaElement).value).toBe('Too long')
  })
})
