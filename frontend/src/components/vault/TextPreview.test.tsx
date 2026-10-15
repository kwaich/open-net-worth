// @vitest-environment jsdom
//
// A text file shows as itself (owner: "why txt file can't be previewed?") — as text, never
// markup; a UTF-16 file decodes; a huge one shows its start. Synthetic data only.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import type { VaultItem } from '../../api/vault'
import { isText } from './vaultFormat'
import { TextPreview } from './TextPreview'

const authFetch = vi.fn<(url: string) => Promise<Response>>()
vi.mock('../../api/client', () => ({ authFetch: (url: string) => authFetch(url) }))

const item = (over: Partial<VaultItem> = {}): VaultItem => ({
  id: 'asset-att_1',
  kind: 'asset',
  source_id: 'att_1',
  title: 'Bill of sale',
  filename: 'bill-of-sale.txt',
  media_type: 'text/plain',
  size_bytes: 113,
  added_at: '2026-09-09T10:00:00Z',
  year: 2026,
  private: false,
  file_url: '/api/uploads/att_1/file',
  ...over,
})

const bytesOf = (b: Uint8Array) =>
  ({
    ok: true,
    arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength),
  }) as Response

beforeEach(() => authFetch.mockReset())
afterEach(cleanup)

describe('TextPreview', () => {
  it('shows the file as text — markup stays text', async () => {
    authFetch.mockResolvedValue(bytesOf(new TextEncoder().encode('Sold to <b>A. Buyer</b>\nS$1')))
    const { container } = render(<TextPreview item={item()} />)
    await waitFor(() => expect(container.querySelector('.vault-text')).toBeTruthy())
    expect(container.querySelector('.vault-text')?.textContent).toBe('Sold to <b>A. Buyer</b>\nS$1')
    expect(container.querySelector('.vault-text b')).toBeNull()
    expect(authFetch).toHaveBeenCalledWith('/api/uploads/att_1/file')
  })

  it('decodes a UTF-16 file (a byte-order mark says so)', async () => {
    const utf16 = new Uint8Array([0xff, 0xfe, ...[...'Hi'].flatMap((c) => [c.charCodeAt(0), 0])])
    authFetch.mockResolvedValue(bytesOf(utf16))
    const { container } = render(<TextPreview item={item()} />)
    await waitFor(() => expect(container.querySelector('.vault-text')?.textContent).toBe('Hi'))
  })

  it('a huge file shows its start and says so; a failed read offers the download', async () => {
    authFetch.mockResolvedValue(bytesOf(new Uint8Array(300 * 1024).fill(0x61)))
    render(<TextPreview item={item()} />)
    expect(await screen.findByText(/Showing the first 256 KB/)).toBeTruthy()
    cleanup()
    authFetch.mockResolvedValue({ ok: false, status: 404 } as Response)
    render(<TextPreview item={item()} />)
    expect(await screen.findByText(/download it/)).toBeTruthy()
  })
})

describe('isText', () => {
  it('is text for notes and bank exports, never for a sheet, a PDF or a photo', () => {
    expect(isText(item())).toBe(true)
    expect(isText(item({ filename: 'export.ofx', media_type: 'application/x-ofx' }))).toBe(true)
    expect(isText(item({ filename: 'cards.qif', media_type: null }))).toBe(true)
    expect(isText(item({ filename: 'aug.csv', media_type: 'text/csv' }))).toBe(false)
    expect(isText(item({ filename: 'aug.pdf', media_type: 'application/pdf' }))).toBe(false)
    expect(isText(item({ filename: 'deed.jpg', media_type: 'image/jpeg' }))).toBe(false)
  })
})
