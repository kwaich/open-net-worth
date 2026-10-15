// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArtifactViewer } from './ArtifactViewer'
import type { ArtifactRef } from '../../../types/chat'

// pdf/xlsx are files: the raw text route answers 415 for them, so the viewer must never ask,
// never mount an iframe, and route Download/Open through the authenticated download route.

const fetchArtifactContent = vi.fn<(id: string) => Promise<string>>()
const fetchArtifactFile = vi.fn<(id: string) => Promise<{ blob: Blob; filename: string | null }>>()
vi.mock('../../../api/artifacts', () => ({
  fetchArtifactContent: (id: string) => fetchArtifactContent(id),
  fetchArtifactFile: (id: string) => fetchArtifactFile(id),
}))
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => false }))

const ref = (artifactType: ArtifactRef['artifactType']): ArtifactRef => ({
  id: 'a-9',
  title: 'Q3 Report',
  filename: `q3_report.${artifactType}`,
  artifactType,
  sizeBytes: 3 * 1024,
})

beforeEach(() => {
  fetchArtifactContent.mockReset()
  fetchArtifactFile.mockReset().mockResolvedValue({
    blob: new Blob(['%PDF-1.7 x'], { type: 'application/pdf' }),
    filename: 'server_name.pdf',
  })
  URL.createObjectURL = vi.fn(() => 'blob:mock')
  URL.revokeObjectURL = vi.fn()
})

afterEach(cleanup)

describe('ArtifactViewer — file artifacts', () => {
  it.each(['pdf', 'xlsx'] as const)('%s: file panel, no raw fetch, no iframe', (type) => {
    const { container } = render(<ArtifactViewer artifact={ref(type)} onClose={() => {}} />)
    expect(screen.getByText(`q3_report.${type} • 3 KB`)).toBeTruthy()
    expect(screen.getAllByRole('button', { name: /Download/ }).length).toBeGreaterThan(0)
    expect(container.querySelector('iframe')).toBeNull()
    expect(fetchArtifactContent).not.toHaveBeenCalled()
  })

  it('only a PDF offers Open; Download saves via the download route under the server filename', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const { rerender } = render(<ArtifactViewer artifact={ref('xlsx')} onClose={() => {}} />)
    expect(screen.queryByRole('button', { name: 'Open' })).toBeNull()
    rerender(<ArtifactViewer artifact={ref('pdf')} onClose={() => {}} />)
    expect(screen.getByRole('button', { name: 'Open' })).toBeTruthy()
    fireEvent.click(screen.getAllByRole('button', { name: 'Download' })[0]!)
    await waitFor(() => expect(click).toHaveBeenCalled())
    expect(fetchArtifactFile).toHaveBeenCalledWith('a-9')
    expect(fetchArtifactContent).not.toHaveBeenCalled()
    click.mockRestore()
  })

  it('Open fetches the download route and points a new tab at an object URL', async () => {
    const tab = { location: { href: '' }, close: vi.fn() }
    const open = vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
    render(<ArtifactViewer artifact={ref('pdf')} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Open' }))
    await waitFor(() => expect(tab.location.href).toBe('blob:mock'))
    expect(fetchArtifactFile).toHaveBeenCalledWith('a-9')
    open.mockRestore()
  })

  it('csv: fetches the text route and previews a table, capped at 200 rows', async () => {
    const rows = Array.from({ length: 250 }, (_, i) => `r${i},"a, ""b"""`).join('\n')
    fetchArtifactContent.mockResolvedValue(`id,note\n${rows}\n`)
    const { container } = render(<ArtifactViewer artifact={ref('csv')} onClose={() => {}} />)
    await screen.findByText('id')
    expect(container.querySelectorAll('tbody tr')).toHaveLength(200)
    expect(screen.getAllByText('a, "b"').length).toBe(200)
    expect(screen.getByText(/first 200 of 250 rows/)).toBeTruthy()
    expect(container.querySelector('iframe')).toBeNull()
    expect(screen.getByLabelText('Save as CSV')).toBeTruthy()
  })
})
