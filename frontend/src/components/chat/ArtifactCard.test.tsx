// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArtifactCard } from './ArtifactCard'
import { ArtifactPanelProvider } from '../../state/artifactPanel'
import type { ArtifactRef } from '../../types/chat'
import type { VaultListing } from '../../api/vault'
import { clearQueryCache } from '../../state/queryCache'

// ArtifactCard is a compact CHIP now (owner, 2026-08-13: matches the Claude.ai reference) —
// it renders no document/iframe of its own. Sandbox/CSP/security coverage for the actual
// rendered document lives in ArtifactViewer.test.tsx, the ONE place the iframe renders.
// This file covers the chip's own two independent click targets: the main row opens the
// viewer (side panel when a provider is present, a fullscreen takeover otherwise), and
// "Download" ONLY downloads — a browser has no API to open a just-downloaded file in the OS
// afterward, so that's not something this button can do (owner, 2026-08-13).

// ArtifactFullscreen renders ArtifactViewer, which uses THIS module's own useArtifactContent —
// mock only downloadArtifact, keeping the real useArtifactContent (it calls the already-mocked
// fetchArtifactContent below, so no network access either way).
const downloadArtifact = vi.fn<(a: ArtifactRef) => Promise<void>>()
vi.mock('../../utils/useArtifactContent', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../utils/useArtifactContent')>()
  return { ...actual, downloadArtifact: (a: ArtifactRef) => downloadArtifact(a) }
})

const fetchArtifactContent = vi.fn<(id: string) => Promise<string>>()
vi.mock('../../api/artifacts', () => ({
  fetchArtifactContent: (id: string) => fetchArtifactContent(id),
}))

// "Save" reads the shared Documents listing and writes through its API — both mocked here.
const EMPTY_VAULT: VaultListing = {
  items: [],
  counts: { all: 0, file: 0, artifact: 0, statement: 0, receipt: 0, asset: 0 },
}
const fetchVault = vi.fn<() => Promise<VaultListing>>()
const saveArtifactToVault = vi.fn<(id: string) => Promise<unknown>>()
const removeArtifactFromVault = vi.fn<(id: string) => Promise<void>>()
vi.mock('../../api/vault', () => ({
  fetchVault: () => fetchVault(),
  saveArtifactToVault: (id: string) => saveArtifactToVault(id),
  removeArtifactFromVault: (id: string) => removeArtifactFromVault(id),
}))

const HTML_REF: ArtifactRef = {
  id: 'a-1',
  title: 'May 2026 Spending',
  filename: 'may_2026_spending.html',
  artifactType: 'html',
  sizeBytes: 48 * 1024,
}

beforeEach(() => {
  downloadArtifact.mockReset().mockResolvedValue(undefined)
  fetchArtifactContent.mockReset().mockResolvedValue('<html><body>ok</body></html>')
  clearQueryCache()
  fetchVault.mockReset().mockResolvedValue(EMPTY_VAULT)
  saveArtifactToVault.mockReset().mockResolvedValue({})
  removeArtifactFromVault.mockReset().mockResolvedValue(undefined)
})

afterEach(cleanup)

describe('ArtifactCard', () => {
  it('renders the chip: icon, title, type line — no live document preview', () => {
    const { container } = render(<ArtifactCard artifact={HTML_REF} />)
    expect(screen.getByText('May 2026 Spending')).toBeTruthy()
    expect(screen.getByText('Document • HTML')).toBeTruthy()
    expect(container.querySelector('iframe')).toBeNull()
  })

  it('labels an svg artifact as an image, not a document', () => {
    render(<ArtifactCard artifact={{ ...HTML_REF, artifactType: 'svg' }} />)
    expect(screen.getByText('Image • SVG')).toBeTruthy()
  })

  it.each([
    ['pdf', 'Document • PDF'],
    ['xlsx', 'Spreadsheet • Excel'],
    ['csv', 'Table • CSV'],
  ] as const)('labels a %s artifact "%s"', (artifactType, label) => {
    render(<ArtifactCard artifact={{ ...HTML_REF, artifactType }} />)
    expect(screen.getByText(label)).toBeTruthy()
  })

  it('clicking the row (outside the download button) opens the side panel when one is provided', () => {
    const onOpen = vi.fn()
    render(
      <ArtifactPanelProvider onOpen={onOpen}>
        <ArtifactCard artifact={HTML_REF} />
      </ArtifactPanelProvider>,
    )
    screen.getByRole('button', { name: /May 2026 Spending/ }).click()
    expect(onOpen).toHaveBeenCalledWith(HTML_REF)
    expect(downloadArtifact).not.toHaveBeenCalled()
  })

  it('clicking the row with no panel provider opens a fullscreen takeover instead', async () => {
    render(<ArtifactCard artifact={HTML_REF} />)
    expect(screen.queryByLabelText('Close')).toBeNull()
    screen.getByRole('button', { name: /May 2026 Spending/ }).click()
    expect(await screen.findByLabelText('Close')).toBeTruthy()
  })

  it('the "Download" button only downloads — it does not open the viewer', () => {
    const onOpen = vi.fn()
    render(
      <ArtifactPanelProvider onOpen={onOpen}>
        <ArtifactCard artifact={HTML_REF} />
      </ArtifactPanelProvider>,
    )
    screen.getByRole('button', { name: /^Download$/ }).click()
    expect(downloadArtifact).toHaveBeenCalledWith(HTML_REF)
    expect(onOpen).not.toHaveBeenCalled()
  })

  it('open, save and download are separate, real <button>s — none nested inside another', () => {
    const { container } = render(<ArtifactCard artifact={HTML_REF} />)
    const main = screen.getByRole('button', { name: /May 2026 Spending/ })
    const saveBtn = screen.getByRole('button', { name: 'Save to Documents' })
    const downloadBtn = screen.getByRole('button', { name: /^Download$/ })
    for (const btn of [main, saveBtn, downloadBtn]) expect(btn.tagName).toBe('BUTTON')
    expect(main.contains(downloadBtn)).toBe(false)
    expect(main.contains(saveBtn)).toBe(false)
    expect(downloadBtn.contains(main)).toBe(false)
    expect(container.querySelectorAll('button').length).toBe(3)
  })

  it('"Save" keeps the artifact in Documents and then reads as Saved', async () => {
    render(<ArtifactCard artifact={HTML_REF} />)
    const save = screen.getByRole('button', { name: 'Save to Documents' })
    expect(save.getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(save)
    expect(saveArtifactToVault).toHaveBeenCalledWith('a-1')
    const saved = await screen.findByRole('button', { name: 'Saved to Documents' })
    expect(saved.getAttribute('aria-pressed')).toBe('true')
    expect(removeArtifactFromVault).not.toHaveBeenCalled()
  })

  it('an artifact already in Documents shows Saved, and clicking it takes it back out', async () => {
    fetchVault.mockResolvedValue({
      items: [
        {
          id: 'artifact-a-1',
          kind: 'artifact',
          source_id: 'a-1',
          title: 'May 2026 Spending',
          filename: 'may_2026_spending.html',
          media_type: 'text/html',
          size_bytes: 10,
          added_at: '2026-09-26T00:00:00Z',
          year: 2026,
          private: false,
          file_url: null,
        },
      ],
      counts: { all: 1, file: 0, artifact: 1, statement: 0, receipt: 0, asset: 0 },
    })
    render(<ArtifactCard artifact={HTML_REF} />)
    const saved = await screen.findByRole('button', { name: 'Saved to Documents' })
    fireEvent.click(saved)
    expect(removeArtifactFromVault).toHaveBeenCalledWith('a-1')
    expect(await screen.findByRole('button', { name: 'Save to Documents' })).toBeTruthy()
  })

  it('a failed save says so and does not claim the artifact is saved', async () => {
    saveArtifactToVault.mockRejectedValue(new Error('artifact not found: a-1'))
    render(<ArtifactCard artifact={HTML_REF} />)
    fireEvent.click(screen.getByRole('button', { name: 'Save to Documents' }))
    await waitFor(() => expect(screen.getByText('Try again')).toBeTruthy())
    const btn = screen.getByRole('button', { name: 'Save to Documents' })
    expect(btn.getAttribute('aria-pressed')).toBe('false')
    expect(btn.getAttribute('title')).toBe('artifact not found: a-1')
  })
})
