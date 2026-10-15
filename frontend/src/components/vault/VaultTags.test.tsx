// @vitest-environment jsdom
//
// Tags in Documents — the user's way of grouping things across kinds instead of folders: a
// filter chip for each tag in use, chips on an item's page that show everything with that tag,
// and a Tags dialog that offers the tags already in use. Synthetic data only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { VaultItem, VaultListing } from '../../api/vault'
import { ChatComposerProvider } from '../../state/chatComposer'
import { clearQueryCache } from '../../state/queryCache'
import { VaultPanel } from './VaultPanel'

vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
const fetchVault = vi.fn<() => Promise<VaultListing>>()
const setVaultTags = vi.fn<(id: string, tags: string[]) => Promise<VaultItem>>()
vi.mock('../../api/vault', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/vault')>()
  return {
    ...actual,
    fetchVault: () => fetchVault(),
    setVaultTags: (id: string, tags: string[]) => setVaultTags(id, tags),
  }
})

function item(id: string, title: string, over: Partial<VaultItem> = {}): VaultItem {
  return {
    id,
    kind: 'file',
    source_id: id.slice(5),
    title,
    filename: `${title}.pdf`,
    media_type: 'application/pdf',
    size_bytes: 2048,
    added_at: '2026-09-20T10:00:00Z',
    year: null,
    private: false,
    file_url: `/api/uploads/${id}/file`,
    origin: 'vault',
    tags: [],
    ...over,
  }
}

const SCHEDULE = item('file-att_1', 'Payment schedule', { tags: ['Apartment'] })
const DEPOSIT = item('receipt-rc_1', 'Deposit receipt', {
  kind: 'receipt',
  tags: ['apartment', 'Taxes 2025'],
})
const WATCH = item('file-att_2', 'Watch papers')

function listing(items: VaultItem[]): VaultListing {
  const counts = { all: items.length, file: 0, artifact: 0, statement: 0, receipt: 0, asset: 0 }
  for (const i of items) counts[i.kind] += 1
  return { items, counts }
}

const rows = () =>
  Array.from(document.querySelectorAll('.vault-row:not(.vault-row--skeleton)')).map(
    (r) => r.querySelector('.vault-row-title')?.textContent,
  )

beforeEach(() => {
  sessionStorage.clear()
  clearQueryCache()
  fetchVault.mockReset().mockResolvedValue(listing([SCHEDULE, DEPOSIT, WATCH]))
  setVaultTags.mockReset().mockImplementation(async (_id, tags) => ({ ...WATCH, tags }))
})
afterEach(cleanup)

function renderPanel() {
  render(
    <ChatComposerProvider>
      <VaultPanel onClose={vi.fn()} onOpenArtifact={vi.fn()} />
    </ChatComposerProvider>,
  )
}

describe('Documents — tags', () => {
  it('each tag in use is a filter chip, counted across kinds; All clears it', async () => {
    renderPanel()
    const chip = await screen.findByRole('button', { name: 'Tag Apartment' })
    expect(chip.textContent).toBe('#Apartment2')
    expect(screen.getByRole('button', { name: 'Tag Taxes 2025' })).toBeTruthy()
    fireEvent.click(chip)
    expect(rows()).toEqual(['Payment schedule', 'Deposit receipt'])
    expect(chip.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(screen.getByRole('button', { name: /^All/ }))
    expect(rows()).toEqual(['Payment schedule', 'Deposit receipt', 'Watch papers'])
  })

  it("an item's tag shows everything that has it", async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Deposit receipt/ }))
    const tags = screen.getByRole('group', { name: 'Tags' })
    fireEvent.click(within(tags).getByRole('button', { name: 'Show everything tagged Taxes 2025' }))
    await waitFor(() => expect(document.querySelector('.vault-detail')).toBeNull())
    expect(rows()).toEqual(['Deposit receipt'])
  })

  it('Add tags offers the tags in use, and saves what was chosen', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Watch papers/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Add tags' }))
    const dialog = await screen.findByRole('dialog', { name: 'Tags' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add tag Apartment' }))
    expect(within(dialog).queryByRole('button', { name: 'Add tag Apartment' })).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(setVaultTags).toHaveBeenCalledWith('file-att_2', ['Apartment']))
  })
})
