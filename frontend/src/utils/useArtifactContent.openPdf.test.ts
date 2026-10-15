// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/artifacts', async (orig) => ({
  ...(await orig<typeof import('../api/artifacts')>()),
  fetchArtifactFile: vi.fn(),
}))

import { fetchArtifactFile } from '../api/artifacts'
import type { ArtifactRef } from '../types/chat'
import { openArtifactPdf } from './useArtifactContent'

const ref: ArtifactRef = {
  id: 'art_1',
  title: 'Statement',
  filename: 'statement.pdf',
  artifactType: 'pdf',
}

function stubTab() {
  const tab = { location: { href: '' }, close: vi.fn() }
  vi.spyOn(window, 'open').mockReturnValue(tab as unknown as Window)
  return tab
}

describe('openArtifactPdf', () => {
  afterEach(() => vi.restoreAllMocks())
  // jsdom has no object URLs; each test installs its own.

  it('opens a real PDF as an application/pdf blob', async () => {
    const tab = stubTab()
    const created: Blob[] = []
    URL.createObjectURL = vi.fn((b: Blob | MediaSource) => {
      created.push(b as Blob)
      return 'blob:ok'
    })
    URL.revokeObjectURL = vi.fn()
    vi.mocked(fetchArtifactFile).mockResolvedValue({
      blob: new Blob(['%PDF-1.7 body'], { type: 'application/pdf' }),
      filename: 'statement.pdf',
    })
    await openArtifactPdf(ref)
    expect(tab.location.href).toBe('blob:ok')
    expect(created[0]?.type).toBe('application/pdf')
  })

  it.each([
    ['html served as html', new Blob(['<script>alert(1)</script>'], { type: 'text/html' })],
    ['html mislabelled as pdf', new Blob(['<html>x</html>'], { type: 'application/pdf' })],
  ])('refuses %s and closes the tab', async (_label, blob) => {
    const tab = stubTab()
    const create = vi.fn(() => 'blob:never')
    URL.createObjectURL = create
    vi.mocked(fetchArtifactFile).mockResolvedValue({ blob, filename: null })
    await expect(openArtifactPdf(ref)).rejects.toThrow('not a PDF')
    expect(create).not.toHaveBeenCalled()
    expect(tab.close).toHaveBeenCalled()
  })
})
