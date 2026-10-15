// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { ArtifactViewer } from './ArtifactViewer'
import type { ArtifactRef } from '../../../types/chat'

// Sandbox-safety: the artifact iframe must run with sandbox="allow-scripts" ONLY —
// allow-same-origin would hand AI-authored HTML the session origin (cookies, parent
// DOM, credentialed API calls) and fully negate the isolation model. SVG artifacts
// additionally run with scripts disabled (sandbox=""). This coverage moved here from
// ArtifactCard.test.tsx when the inline card stopped rendering the document itself
// (2026-08-13) — ArtifactViewer is now the ONLY place the sandboxed iframe renders,
// shared by the side panel and the fullscreen takeover.

const fetchArtifactContent = vi.fn<(id: string) => Promise<string>>()
vi.mock('../../../api/artifacts', () => ({
  fetchArtifactContent: (id: string) => fetchArtifactContent(id),
}))

const isMobile = vi.fn(() => false)
vi.mock('../../../hooks/useIsMobile', () => ({
  useIsMobile: () => isMobile(),
}))

const HTML_REF: ArtifactRef = {
  id: 'a-1',
  title: 'May 2026 Spending',
  filename: 'may_2026_spending.html',
  artifactType: 'html',
  sizeBytes: 48 * 1024,
}

beforeEach(() => {
  fetchArtifactContent.mockReset()
  isMobile.mockReset().mockReturnValue(false)
})

afterEach(cleanup)

describe('ArtifactViewer', () => {
  it('renders the iframe sandboxed with allow-scripts and NEVER allow-same-origin', async () => {
    fetchArtifactContent.mockResolvedValue('<html><head></head><body>chart</body></html>')
    const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    const frame = container.querySelector('iframe')!
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin')
    // Loaded from the backend's prepared-frame route (own CSP), never as an inherited srcdoc.
    expect(frame.getAttribute('src')).toBe('/api/artifacts/a-1/frame')
    expect(frame.hasAttribute('srcdoc')).toBe(false)
  })

  it('covers the frame until its page loads — never a white flash, never a hidden frame', async () => {
    fetchArtifactContent.mockResolvedValue('<html><body>report</body></html>')
    // jsdom fires the frame's own load right after insertion, so read the DOM AT insertion.
    let coverAtInsert = false
    let frameClassAtInsert = ''
    const watcher = new MutationObserver(() => {
      const f = document.querySelector('iframe')
      if (f && !frameClassAtInsert) {
        frameClassAtInsert = f.className
        coverAtInsert = !!document.querySelector('.artifact-viewer-frame-cover')
      }
    })
    watcher.observe(document.body, { childList: true, subtree: true })
    const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    watcher.disconnect()
    expect(coverAtInsert).toBe(true)
    // The frame itself is never hidden: iOS will not scroll an iframe that loaded hidden.
    expect(frameClassAtInsert).toBe('artifact-viewer-frame')
    fireEvent.load(container.querySelector('iframe')!)
    expect(container.querySelector('.artifact-viewer-frame-cover')).toBeNull()
  })

  it('renders svg artifacts with scripts fully disabled (empty sandbox)', async () => {
    fetchArtifactContent.mockResolvedValue('<svg></svg>')
    const { container } = render(
      <ArtifactViewer
        artifact={{ ...HTML_REF, artifactType: 'svg', filename: 'pie.svg' }}
        onClose={() => {}}
      />,
    )
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    expect(container.querySelector('iframe')!.getAttribute('sandbox')).toBe('')
  })

  it('renders the error state with Retry when the fetch fails, and retries', async () => {
    fetchArtifactContent.mockRejectedValueOnce(new Error('404'))
    fetchArtifactContent.mockResolvedValueOnce('<html><body>ok</body></html>')
    const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    const retry = await screen.findByText('Retry')
    expect(screen.getByText('Could not load artifact')).toBeTruthy()
    retry.click()
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    expect(fetchArtifactContent).toHaveBeenCalledTimes(2)
  })

  it('never renders artifact bytes outside the iframe in preview mode (no dangerouslySetInnerHTML of raw)', async () => {
    fetchArtifactContent.mockResolvedValue('<html><body><b id="x">SECRET-MARKER</b></body></html>')
    const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    // The document is only ever loaded inside the iframe — not into the live DOM.
    expect(container.querySelector('#x')).toBeNull()
  })

  it('shows Expand (not Minimize) when expandState.fullscreen is false, and calls onToggle', async () => {
    fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
    const onToggle = vi.fn()
    render(
      <ArtifactViewer
        artifact={HTML_REF}
        onClose={() => {}}
        expandState={{ fullscreen: false, onToggle }}
      />,
    )
    const btn = await screen.findByLabelText('Expand')
    expect(screen.queryByLabelText('Minimize')).toBeNull()
    btn.click()
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('shows Minimize (not Expand) when expandState.fullscreen is true', async () => {
    fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
    render(
      <ArtifactViewer
        artifact={HTML_REF}
        onClose={() => {}}
        expandState={{ fullscreen: true, onToggle: () => {} }}
      />,
    )
    expect(await screen.findByLabelText('Minimize')).toBeTruthy()
    expect(screen.queryByLabelText('Expand')).toBeNull()
  })

  it('omits the expand/minimize control entirely when expandState is not provided', async () => {
    fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
    render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    await waitFor(() => expect(fetchArtifactContent).toHaveBeenCalled())
    expect(screen.queryByLabelText('Expand')).toBeNull()
    expect(screen.queryByLabelText('Minimize')).toBeNull()
  })

  it('calls onClose when the close button is clicked', async () => {
    fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
    const onClose = vi.fn()
    render(<ArtifactViewer artifact={HTML_REF} onClose={onClose} />)
    ;(await screen.findByLabelText('Close')).click()
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('switches to the source view and renders one numbered row per source line', async () => {
    fetchArtifactContent.mockResolvedValue('<html>\n<body>hi</body>\n</html>')
    const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    ;(await screen.findByLabelText('View source')).click()
    await waitFor(() =>
      expect(container.querySelectorAll('.artifact-viewer-source-line').length).toBe(3),
    )
    const linenos = Array.from(container.querySelectorAll('.artifact-viewer-source-lineno')).map(
      (n) => n.textContent,
    )
    expect(linenos).toEqual(['1', '2', '3'])
  })

  it('highlights <style> block content as CSS, not flat markup (regression)', async () => {
    // Per-line highlighting (needed for wrapping + line numbers) loses <style>/<script>
    // embedding unless each line's SECTION is tracked separately — every line highlighted
    // as plain 'markup' left an entire report's CSS block one flat, uncolored token (owner
    // report, real device screenshot).
    fetchArtifactContent.mockResolvedValue(
      '<html>\n<style>\nbody { color: red; }\n</style>\n</html>',
    )
    const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
    await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
    ;(await screen.findByLabelText('View source')).click()
    await waitFor(() =>
      expect(container.querySelectorAll('.artifact-viewer-source-line').length).toBe(5),
    )
    // Line 3 ("body { color: red; }") is INSIDE <style> — it must carry CSS tokens
    // (.pr-property for "color"), not just plain text or markup tags.
    const cssLine = container.querySelectorAll('.artifact-viewer-source-linecode')[2] as HTMLElement
    expect(cssLine.querySelector('.pr-token.pr-property')).not.toBeNull()
  })

  describe('on mobile', () => {
    beforeEach(() => {
      isMobile.mockReturnValue(true)
    })

    it('renders the bottom-sheet header (X / centered title / kebab) instead of the desktop toolbar', async () => {
      fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
      render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
      await waitFor(() => expect(fetchArtifactContent).toHaveBeenCalled())
      expect(screen.getByText('May 2026 Spending')).toBeTruthy()
      expect(screen.getByLabelText('Close')).toBeTruthy()
      expect(screen.getByLabelText('More options')).toBeTruthy()
      // The desktop-only controls must not render on mobile.
      expect(screen.queryByLabelText('Preview')).toBeNull()
      expect(screen.queryByLabelText('View source')).toBeNull()
      expect(screen.queryByText('Copy')).toBeNull()
    })

    it('the kebab menu toggles preview/code with a checkmark on the active one', async () => {
      fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
      const { container } = render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
      await waitFor(() => expect(container.querySelector('iframe')).not.toBeNull())
      screen.getByLabelText('More options').click()
      const previewItem = await screen.findByRole('menuitemradio', { name: /Preview/ })
      const codeItem = screen.getByRole('menuitemradio', { name: /Code/ })
      expect(previewItem.getAttribute('aria-checked')).toBe('true')
      expect(codeItem.getAttribute('aria-checked')).toBe('false')

      codeItem.click()
      await waitFor(() =>
        expect(container.querySelectorAll('.artifact-viewer-source-line').length).toBeGreaterThan(
          0,
        ),
      )
      // The menu closes after selecting — reopen to check the new state.
      screen.getByLabelText('More options').click()
      const reopenedCodeItem = await screen.findByRole('menuitemradio', { name: /Code/ })
      expect(reopenedCodeItem.getAttribute('aria-checked')).toBe('true')
    })

    it('the kebab menu offers Copy and Save as HTML — no Publish/Save as PDF (no such capability here)', async () => {
      fetchArtifactContent.mockResolvedValue('<html><body>ok</body></html>')
      render(<ArtifactViewer artifact={HTML_REF} onClose={() => {}} />)
      await waitFor(() => expect(fetchArtifactContent).toHaveBeenCalled())
      screen.getByLabelText('More options').click()
      expect(await screen.findByRole('menuitem', { name: /Copy/ })).toBeTruthy()
      expect(screen.getByRole('menuitem', { name: /Save as HTML/ })).toBeTruthy()
      expect(screen.queryByText('Publish')).toBeNull()
      expect(screen.queryByText(/Save as PDF/)).toBeNull()
    })
  })
})
