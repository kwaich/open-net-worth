// @vitest-environment jsdom
//
// Live report 2026-08-11: sent-message image attachments rendered as a plain text pill
// ("🖼 Image (name.png, 84KB)") — never an actual thumbnail, and never tappable to open —
// even though GET /api/uploads/{ref_id}/image exists to serve the raw bytes back. This pins
// the fix: an image attachment is a real <img> thumbnail, backed by that route, and tapping
// it opens the full image in a modal. A non-image (file) attachment keeps the plain pill —
// there's nothing to preview.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { cleanup, render, fireEvent, screen } from '@testing-library/react'
import { MessageBubble } from './MessageBubble'
import type { Message } from '../../types/chat'

afterEach(cleanup)

const noop = vi.fn()

function renderMessage(over: Partial<Message>) {
  const message: Message = { id: 'm1', role: 'user', text: 'here', ...over }
  return render(<MessageBubble message={message} onSendMessage={noop} />)
}

describe('MessageBubble — image attachments render as real thumbnails', () => {
  it('renders an <img> sourced from the upload-image route, not a text pill', () => {
    const { container } = renderMessage({
      attachments: [{ ref_id: 'up_abc123', kind: 'image', summary: 'Image (pic.png, 84KB)' }],
    })
    const thumb = container.querySelector('.msg-attachment-thumb img') as HTMLImageElement
    expect(thumb).not.toBeNull()
    expect(thumb.getAttribute('src')).toBe('/api/uploads/up_abc123/image')
    expect(container.querySelector('.msg-attachment-pill')).toBeNull()
  })

  it('tapping the thumbnail opens the full image in a modal', () => {
    renderMessage({
      attachments: [{ ref_id: 'up_abc123', kind: 'image', summary: 'Image (pic.png, 84KB)' }],
    })
    expect(screen.queryByRole('dialog')).toBeNull()

    fireEvent.click(document.querySelector('.msg-attachment-thumb') as HTMLElement)

    const dialog = screen.getByRole('dialog')
    expect(dialog).not.toBeNull()
    const full = dialog.querySelector('.msg-attachment-lightbox-img') as HTMLImageElement
    expect(full.getAttribute('src')).toBe('/api/uploads/up_abc123/image')
  })

  it('a non-image attachment keeps the plain file pill, no thumbnail', () => {
    const { container } = renderMessage({
      attachments: [{ ref_id: 'up_xyz', kind: 'text', summary: "Statement 'bank.csv'" }],
    })
    expect(container.querySelector('.msg-attachment-thumb')).toBeNull()
    const pill = container.querySelector('.msg-attachment-pill')
    expect(pill?.textContent).toBe("📄 Statement 'bank.csv'")
  })

  it('a non-image attachment pill is a real download link to GET /api/uploads/{ref_id}/file', () => {
    // 2026-08-21: previously an inert <div> — a non-image chat attachment (a PDF, a
    // spreadsheet) had no way to ever be fetched back. Now a plain <a download> pointed
    // at the new generic file route, matching what a real click-to-save needs.
    const { container } = renderMessage({
      attachments: [{ ref_id: 'up_xyz', kind: 'text', summary: "Statement 'bank.csv'" }],
    })
    const pill = container.querySelector('.msg-attachment-pill') as HTMLAnchorElement
    expect(pill.tagName).toBe('A')
    expect(pill.getAttribute('href')).toBe('/api/uploads/up_xyz/file')
    expect(pill.hasAttribute('download')).toBe(true)
  })

  it('falls back to the text pill if the image fails to load (e.g. a deleted upload)', () => {
    const { container } = renderMessage({
      attachments: [{ ref_id: 'up_gone', kind: 'image', summary: 'Image (pic.png, 84KB)' }],
    })
    const img = container.querySelector('.msg-attachment-thumb img') as HTMLImageElement
    fireEvent.error(img)

    expect(container.querySelector('.msg-attachment-thumb')).toBeNull()
    const pill = container.querySelector('.msg-attachment-pill')
    expect(pill?.textContent).toBe('🖼 Image (pic.png, 84KB)')
  })
})
