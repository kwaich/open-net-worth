// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import { FileViewerBody } from './StatementsSection'

// On a phone the browser's own PDF view keeps a statement at its full page width, so it
// scrolled sideways (owner report, 2026-09-27). There, a PDF is shown as server-drawn pages in
// the stacked, zoomable reader instead ("can't zoom in"); the desktop keeps the browser's viewer.
vi.mock('../../vault/DocumentReader', () => ({
  DocumentReader: ({ itemId }: { itemId: string }) => <div data-testid="pages">{itemId}</div>,
}))
let mobile = false
vi.mock('../../../hooks/useIsMobile', () => ({ useIsMobile: () => mobile }))

afterEach(cleanup)

describe('FileViewerBody — a PDF on a phone', () => {
  const pdf = { kind: 'pdf' as const, url: 'blob:statement' }

  it('shows the zoomable page reader on a phone', () => {
    mobile = true
    render(<FileViewerBody state={pdf} filename="s.pdf" pageItemId="statement-stmt_1" />)
    expect(screen.getByTestId('pages').textContent).toBe('statement-stmt_1')
  })

  it('keeps the browser PDF viewer on a desktop', () => {
    mobile = false
    const { container } = render(
      <FileViewerBody state={pdf} filename="s.pdf" pageItemId="statement-stmt_1" />,
    )
    expect(container.querySelector('iframe')?.getAttribute('src')).toBe('blob:statement')
    expect(screen.queryByTestId('pages')).toBeNull()
  })
})

describe('FileViewerBody — a photographed receipt', () => {
  it('zooms in place (the app has the browser’s pinch-zoom off)', () => {
    mobile = true
    const { container } = render(
      <FileViewerBody state={{ kind: 'image', url: 'blob:receipt' }} filename="r.jpg" />,
    )
    const frame = container.querySelector('.acct-receiptview-zoom .ui-zoomimage')
    expect(frame?.hasAttribute('data-sheet-drag-ignore')).toBe(true)
    expect(frame?.querySelector('img')?.getAttribute('src')).toBe('blob:receipt')
  })
})
