// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { VaultItem, VaultListing } from '../../api/vault'
import { ChatComposerProvider, useChatComposer } from '../../state/chatComposer'
import { clearQueryCache } from '../../state/queryCache'
import { VaultPanel } from './VaultPanel'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

const fetchVault = vi.fn<() => Promise<VaultListing>>()
const addVaultFile = vi.fn<(f: File) => Promise<VaultItem>>()
const updateVaultFile = vi.fn<(id: string, changes: object) => Promise<VaultItem>>()
const removeVaultItem = vi.fn<(id: string, options?: object) => Promise<{ action: string }>>()
const renameVaultItem = vi.fn<(id: string, title: string | null) => Promise<VaultItem>>()
vi.mock('../../api/vault', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../api/vault')>()
  return {
    ...actual,
    fetchVault: () => fetchVault(),
    addVaultFile: (f: File) => addVaultFile(f),
    updateVaultFile: (id: string, changes: object) => updateVaultFile(id, changes),
    removeVaultItem: (id: string, options?: object) => removeVaultItem(id, options),
    renameVaultItem: (id: string, title: string | null) => renameVaultItem(id, title),
  }
})

function item(over: Partial<VaultItem>): VaultItem {
  return {
    id: 'file-att_1',
    kind: 'file',
    source_id: 'att_1',
    title: '2025 tax return',
    filename: 'return.pdf',
    media_type: 'application/pdf',
    size_bytes: 2048,
    added_at: '2026-09-20T10:00:00Z',
    year: 2025,
    private: false,
    file_url: '/api/uploads/att_1/file',
    origin: 'vault',
    ...over,
  }
}

const TAX = item({})
const POLICY = item({
  id: 'file-att_2',
  source_id: 'att_2',
  title: 'Home insurance policy',
  private: true,
  origin: 'chat',
})
const STATEMENT = item({
  id: 'statement-stmt_1',
  kind: 'statement',
  source_id: 'stmt_1',
  title: 'Everyday Account — Aug 2026',
  file_url: '/api/statements/stmt_1/file',
  account_name: 'Everyday Account',
  statement_date: '2026-08-31',
  origin: undefined,
})
const REPORT = item({
  id: 'artifact-art_1',
  kind: 'artifact',
  source_id: 'art_1',
  title: 'Spending review',
  filename: 'spending.html',
  media_type: 'text/html',
  artifact_type: 'html',
  file_url: null,
  origin: undefined,
})

function listing(items: VaultItem[]): VaultListing {
  const counts = { all: items.length, file: 0, artifact: 0, statement: 0, receipt: 0, asset: 0 }
  for (const i of items) counts[i.kind] += 1
  return { items, counts }
}

function MentionProbe() {
  const { pendingMentions } = useChatComposer()
  return <output data-testid="mentions">{JSON.stringify(pendingMentions)}</output>
}

function renderPanel(props: Partial<Parameters<typeof VaultPanel>[0]> = {}) {
  const onOpenArtifact = vi.fn()
  const onClose = vi.fn()
  render(
    <ChatComposerProvider>
      <VaultPanel onClose={onClose} onOpenArtifact={onOpenArtifact} {...props} />
      <MentionProbe />
    </ChatComposerProvider>,
  )
  return { onOpenArtifact, onClose }
}

beforeEach(() => {
  sessionStorage.clear() // the open item is remembered for the browser session
  navigate.mockReset()
  renameVaultItem
    .mockReset()
    .mockImplementation(async (_id, title) => ({ ...TAX, title: title ?? 'x' }))
  clearQueryCache()
  fetchVault.mockReset().mockResolvedValue(listing([TAX, POLICY, STATEMENT, REPORT]))
  addVaultFile.mockReset()
  updateVaultFile.mockReset().mockImplementation(async (_id, changes) => ({ ...TAX, ...changes }))
  removeVaultItem.mockReset().mockResolvedValue({ action: 'deleted' })
})

afterEach(cleanup)

describe('VaultPanel', () => {
  it('lists every kind, each row a document entity (mentionable, draggable)', async () => {
    renderPanel()
    const row = await screen.findByRole('button', { name: /^2025 tax return/ })
    expect(row.getAttribute('data-entity-id')).toBe('document:file-att_1')
    expect(screen.getByText('Everyday Account — Aug 2026')).toBeTruthy()
    expect(screen.getByText('Spending review')).toBeTruthy()
    expect(screen.getByRole('button', { name: /All\s*4/ }).getAttribute('aria-pressed')).toBe(
      'true',
    )
  })

  it('filters by kind and by search text', async () => {
    renderPanel()
    await screen.findByText('2025 tax return')
    fireEvent.click(screen.getByRole('button', { name: /Statements/ }))
    expect(screen.queryByText('2025 tax return')).toBeNull()
    expect(screen.getByText('Everyday Account — Aug 2026')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /All/ }))
    fireEvent.change(screen.getByLabelText('Search documents'), { target: { value: 'insur' } })
    expect(screen.getByText('Home insurance policy')).toBeTruthy()
    expect(screen.queryByText('Spending review')).toBeNull()
  })

  it('rows drag into the chat beside it, but not in the phone sheet that covers the chat', async () => {
    renderPanel()
    const row = await screen.findByRole('button', { name: /^2025 tax return/ })
    expect(row.getAttribute('draggable')).toBe('true')
    cleanup()
    renderPanel({ layout: 'sheet' })
    const sheetRow = await screen.findByRole('button', { name: /^2025 tax return/ })
    expect(sheetRow.getAttribute('draggable')).toBe('false')
    expect(sheetRow.getAttribute('data-entity-id')).toBe('document:file-att_1') // long-press menu
  })

  it('the chip row fades only on a side with more chips to scroll to', async () => {
    renderPanel()
    const row = await screen.findByRole('group', { name: 'Show' })
    const at = (left: number) => {
      Object.defineProperty(row, 'scrollWidth', { value: 600, configurable: true })
      Object.defineProperty(row, 'clientWidth', { value: 300, configurable: true })
      Object.defineProperty(row, 'scrollLeft', { value: left, configurable: true })
      fireEvent.scroll(row)
      return row.dataset.fade
    }
    expect(at(0)).toBe('right')
    expect(at(150)).toBe('left right')
    expect(at(300)).toBe('left')
  })

  it('while the list loads it shows skeleton rows, then the documents', async () => {
    let resolve: (l: VaultListing) => void = () => {}
    fetchVault.mockReturnValue(new Promise<VaultListing>((r) => (resolve = r)))
    renderPanel()
    const loading = screen.getByRole('status', { name: 'Loading documents' })
    expect(loading.querySelectorAll('.vault-row--skeleton').length).toBeGreaterThan(3)
    resolve(listing([TAX]))
    expect(await screen.findByRole('button', { name: /^2025 tax return/ })).toBeTruthy()
    expect(screen.queryByRole('status', { name: 'Loading documents' })).toBeNull()
  })

  it('says so when there are no documents yet', async () => {
    fetchVault.mockResolvedValue(listing([]))
    renderPanel()
    expect(await screen.findByText('No documents yet')).toBeTruthy()
  })

  it('"Ask Lucius about this" stages a document mention — never the contents', async () => {
    const onAsked = vi.fn()
    renderPanel({ onAsked })
    fireEvent.click(await screen.findByRole('button', { name: /^2025 tax return/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Ask Lucius about this' }))
    expect(JSON.parse(screen.getByTestId('mentions').textContent || '[]')).toEqual([
      { type: 'document', ref: 'file-att_1', label: '2025 tax return' },
    ])
    expect(onAsked).toHaveBeenCalled()
  })

  it('a Private file offers no Ask, and its switch writes private=false only when flipped', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Home insurance policy/ }))
    expect(screen.queryByRole('button', { name: 'Ask Lucius about this' })).toBeNull()
    expect(screen.getByText(/never what is in it/)).toBeTruthy()
    const toggle = screen.getByRole('switch', { name: 'Private' }) as HTMLInputElement
    expect(toggle.checked).toBe(true)
    fireEvent.click(toggle)
    expect(updateVaultFile).toHaveBeenCalledWith('file-att_2', { private: false })
  })

  it('marking a file Private writes it and shows at once', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^2025 tax return/ }))
    const toggle = screen.getByRole('switch', { name: 'Private' }) as HTMLInputElement
    fireEvent.click(toggle)
    expect(updateVaultFile).toHaveBeenCalledWith('file-att_1', { private: true })
    expect(toggle.checked).toBe(true)
  })

  it('removing a chat file only takes it out of Documents, after a confirm', async () => {
    removeVaultItem.mockResolvedValue({ action: 'removed' })
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Home insurance policy/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove from Documents' }))
    const dialog = await screen.findByRole('dialog')
    expect(
      within(dialog).getByText('The file stays in the chat you sent it in — still Private.'),
    ).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove from Documents' }))
    await waitFor(() =>
      expect(removeVaultItem).toHaveBeenCalledWith('file-att_2', { deleteFile: undefined }),
    )
  })

  it('"Delete file" deletes a kept chat file for good, after its own confirm', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Home insurance policy/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete file' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/can no longer open or read it/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete file' }))
    await waitFor(() =>
      expect(removeVaultItem).toHaveBeenCalledWith('file-att_2', { deleteFile: true }),
    )
  })

  it('a statement can be deleted here — its transactions stay', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Everyday Account — Aug 2026/ }))
    expect(screen.queryByRole('switch', { name: 'Private' })).toBeNull()
    expect(screen.getByRole('link', { name: /Download/ })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Delete statement' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/transactions it imported stay/)).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete statement' }))
    await waitFor(() => expect(removeVaultItem).toHaveBeenCalledWith(STATEMENT.id, {}))
  })

  it('a saved artifact opens in the artifact viewer', async () => {
    const { onOpenArtifact } = renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Spending review/ }))
    fireEvent.click(screen.getByRole('button', { name: /Open/ }))
    expect(onOpenArtifact).toHaveBeenCalledWith({
      id: 'art_1',
      title: 'Spending review',
      filename: 'spending.html',
      artifactType: 'html',
      sizeBytes: 2048,
    })
  })

  it('adding one file uploads it and opens it so its details can be set', async () => {
    const added = item({ id: 'file-att_9', source_id: 'att_9', title: 'deed.pdf', year: null })
    addVaultFile.mockResolvedValue(added)
    fetchVault.mockResolvedValueOnce(listing([TAX])).mockResolvedValue(listing([added, TAX]))
    const { container } = render(
      <ChatComposerProvider>
        <VaultPanel onClose={vi.fn()} onOpenArtifact={vi.fn()} />
      </ChatComposerProvider>,
    )
    await screen.findByText('2025 tax return')
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(['%PDF-1.4'], 'deed.pdf', { type: 'application/pdf' })
    fireEvent.change(input, { target: { files: [file] } })
    await waitFor(() => expect(addVaultFile).toHaveBeenCalledWith(file))
    expect(await screen.findByRole('button', { name: 'Back to Documents' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'deed.pdf' })).toBeTruthy()
  })

  it('a photo opens large inside the app — never as a link to the raw file', async () => {
    // Owner report: on the installed phone app the raw-file link took over the whole app
    // window, with nothing to close it.
    const photo = item({
      id: 'receipt-rcpt_9',
      kind: 'receipt',
      source_id: 'rcpt_9',
      title: 'Receipt — Utilities',
      media_type: 'image/jpeg',
      filename: 'r.jpg',
      file_url: '/api/receipts/rcpt_9/file',
      origin: undefined,
    })
    fetchVault.mockResolvedValue(listing([photo]))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Receipt — Utilities/ }))
    const rawLinks = screen
      .queryAllByRole('link')
      .filter((a) => a.getAttribute('href') === photo.file_url && !a.hasAttribute('download'))
    expect(rawLinks).toEqual([])
    fireEvent.click(screen.getByRole('button', { name: 'Open Receipt — Utilities' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByRole('img', { name: 'Receipt — Utilities' })).toBeTruthy()
    // Opens focused on its content, not with its × ringed.
    await waitFor(() => expect(document.activeElement?.className).toBe('vault-modal-content'))
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('a receipt opens its transaction; a statement opens its account', async () => {
    const receipt = item({
      id: 'receipt-rcpt_1',
      kind: 'receipt',
      source_id: 'rcpt_1',
      title: 'Receipt — 7-Eleven',
      media_type: 'image/jpeg',
      filename: 'r.jpg',
      transaction_id: 'txn_42',
      origin: undefined,
    })
    const statement = { ...STATEMENT, account_id: 'acc_7' }
    fetchVault.mockResolvedValue(listing([receipt, statement]))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Receipt — 7-Eleven/ }))
    fireEvent.click(screen.getByRole('button', { name: 'View transaction' }))
    expect(navigate).toHaveBeenCalledWith({ to: '/accounts', search: { txn: 'txn_42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Back to Documents' }))
    fireEvent.click(await screen.findByRole('button', { name: /^Everyday Account — Aug 2026/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Open account' }))
    expect(navigate).toHaveBeenLastCalledWith({ to: '/accounts', search: { account: 'acc_7' } })
  })

  it('a file attached to an asset is listed by its asset, managed there, and opens it', async () => {
    const appraisal = item({
      id: 'asset-att_7',
      kind: 'asset',
      source_id: 'att_7',
      title: 'Valuation report',
      asset_id: 'oa_1',
      asset_name: '16 Jln Masjid',
      origin: undefined,
    })
    fetchVault.mockResolvedValue(listing([appraisal]))
    renderPanel()
    expect(await screen.findByText(/16 Jln Masjid · /)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /^Valuation report/ }))
    expect(screen.getByRole('button', { name: 'Delete file' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rename' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Open asset' }))
    expect(navigate).toHaveBeenCalledWith({ to: '/assets', search: { id: 'oa_1' } })
  })

  // Owner: "i can't see we can control that in the document modal" — an asset file's Private is
  // its asset page's "Lucius can read" switch, flipped; it has no Year of its own.
  it('an asset file has its Private switch here too, and no Year', async () => {
    const appraisal = item({
      id: 'asset-att_7',
      kind: 'asset',
      source_id: 'att_7',
      title: 'Valuation report',
      asset_id: 'oa_1',
      asset_name: '16 Jln Masjid',
      origin: undefined,
    })
    fetchVault.mockResolvedValue(listing([appraisal]))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Valuation report/ }))
    expect(screen.getByText(/The same as “Lucius can read” on its asset’s page/)).toBeTruthy()
    expect(screen.queryByPlaceholderText('e.g. 2025')).toBeNull()
    fireEvent.click(screen.getByRole('switch', { name: 'Private' }))
    expect(updateVaultFile).toHaveBeenCalledWith('asset-att_7', { private: true })
  })

  it('renames a file from its actions — an empty name goes back to the file name', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^2025 tax return/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
    const dialog = await screen.findByRole('dialog')
    const input = within(dialog).getByLabelText('Name') as HTMLInputElement
    expect(input.value).toBe('2025 tax return')
    expect(within(dialog).getByText(/use the file name/)).toBeTruthy()
    fireEvent.change(input, { target: { value: '  2025 federal return ' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(renameVaultItem).toHaveBeenCalledWith('file-att_1', '2025 federal return')
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
    fireEvent.change(within(await screen.findByRole('dialog')).getByLabelText('Name'), {
      target: { value: '' },
    })
    fireEvent.submit(within(screen.getByRole('dialog')).getByLabelText('Name'))
    await waitFor(() => expect(renameVaultItem).toHaveBeenLastCalledWith('file-att_1', null))
  })

  it('an artifact needs a name, and a refused rename says why', async () => {
    renameVaultItem.mockRejectedValue(new Error('Renaming failed: the name is too long'))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Spending review/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
    const dialog = await screen.findByRole('dialog')
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: '  ' } })
    expect(save.disabled).toBe(true)
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: 'Q3 review' } })
    fireEvent.click(save)
    expect(await within(dialog).findByText(/too long/)).toBeTruthy()
  })

  it('a file with no preview keeps a preview-sized place that says to download it', async () => {
    // A text file shows as text now (TextPreview); a Word file is still one the app can't draw.
    const contract = item({
      id: 'file-att_7',
      source_id: 'att_7',
      title: 'Contract',
      filename: 'contract.docx',
      media_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })
    fetchVault.mockResolvedValue(listing([contract]))
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Contract/ }))
    expect(screen.getByText('No preview for this file')).toBeTruthy()
    expect(screen.getByText('Download it to open it.')).toBeTruthy()
  })

  it('a statement can be renamed, and cleared back to its automatic name', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Everyday Account — Aug 2026/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }))
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/back to the account and month/)).toBeTruthy()
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: '' } })
    const save = within(dialog).getByRole('button', { name: 'Save' }) as HTMLButtonElement
    expect(save.disabled).toBe(false)
    fireEvent.click(save)
    await waitFor(() => expect(renameVaultItem).toHaveBeenCalledWith(STATEMENT.id, null))
  })

  it('as a phone sheet, opens with nothing ringed — focus is on the panel, not a button', async () => {
    const { Modal } = await import('../ui/Modal')
    render(
      <ChatComposerProvider>
        <Modal open onClose={() => {}} title="Documents">
          <VaultPanel layout="sheet" onClose={() => {}} onOpenArtifact={() => {}} />
        </Modal>
      </ChatComposerProvider>,
    )
    await screen.findByRole('button', { name: /^2025 tax return/ })
    expect(document.activeElement?.tagName).toBe('SECTION')
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Documents')
  })

  it('the open item survives a refresh (remount) within the session', async () => {
    renderPanel()
    fireEvent.click(await screen.findByRole('button', { name: /^Home insurance policy/ }))
    cleanup()
    renderPanel()
    expect(await screen.findByRole('heading', { name: 'Home insurance policy' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Back to Documents' }))
    expect(sessionStorage.getItem('documentsItem')).toBeNull()
  })
})
