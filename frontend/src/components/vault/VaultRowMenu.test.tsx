// @vitest-environment jsdom
//
// A Documents row's ⋯ menu — quick rename, note, tags and removal without opening the item,
// through the same dialogs its page's Manage opens. Synthetic data only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { VaultItem, VaultListing } from '../../api/vault'
import { ChatComposerProvider } from '../../state/chatComposer'
import { clearQueryCache } from '../../state/queryCache'
import { VaultPanel } from './VaultPanel'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
const fetchVault = vi.fn<() => Promise<VaultListing>>()
const renameVaultItem = vi.fn<(id: string, title: string | null) => Promise<VaultItem>>()
const removeVaultItem = vi.fn<(id: string, options?: object) => Promise<{ action: string }>>()
vi.mock('../../api/vault', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/vault')>()
  return {
    ...actual,
    fetchVault: () => fetchVault(),
    renameVaultItem: (id: string, title: string | null) => renameVaultItem(id, title),
    removeVaultItem: (id: string, options?: object) => removeVaultItem(id, options),
  }
})

const STATEMENT: VaultItem = {
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
  tags: [],
}

beforeEach(() => {
  sessionStorage.clear()
  clearQueryCache()
  fetchVault.mockReset().mockResolvedValue({
    items: [STATEMENT],
    counts: { all: 1, file: 0, artifact: 0, statement: 1, receipt: 0, asset: 0 },
  })
  renameVaultItem.mockReset().mockImplementation(async (_id, title) => ({
    ...STATEMENT,
    title: title ?? '',
  }))
  removeVaultItem.mockReset().mockResolvedValue({ action: 'deleted' })
})
afterEach(cleanup)

function renderPanel() {
  render(
    <ChatComposerProvider>
      <VaultPanel onClose={vi.fn()} onOpenArtifact={vi.fn()} />
    </ChatComposerProvider>,
  )
}

const openMenu = async () =>
  fireEvent.click(
    await screen.findByRole('button', { name: 'More actions for Everyday — Aug 2026' }),
  )

describe('a Documents row’s ⋯ menu', () => {
  it('offers the same actions as the item’s Manage — without opening it', async () => {
    renderPanel()
    await openMenu()
    expect(screen.getAllByRole('menuitem').map((m) => m.textContent)).toEqual([
      'Rename',
      'Add a note',
      'Add tags',
      'Delete statement',
    ])
    expect(document.querySelector('.vault-detail')).toBeNull()
  })

  it('renames from the list', async () => {
    renderPanel()
    await openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }))
    const dialog = await screen.findByRole('dialog', { name: 'Rename' })
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'DBS Aug' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(renameVaultItem).toHaveBeenCalledWith('statement-stmt_1', 'DBS Aug'))
  })

  it('deletes from the list only after a confirm', async () => {
    renderPanel()
    await openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: 'Delete statement' }))
    const confirm = await screen.findByRole('dialog', { name: 'Delete statement?' })
    expect(removeVaultItem).not.toHaveBeenCalled()
    fireEvent.click(within(confirm).getByRole('button', { name: 'Delete statement' }))
    await waitFor(() =>
      expect(removeVaultItem).toHaveBeenCalledWith('statement-stmt_1', { deleteFile: undefined }),
    )
  })
})
