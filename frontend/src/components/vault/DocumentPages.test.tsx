// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { DocumentPages } from './DocumentPages'

const authFetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>()
vi.mock('../../api/client', () => ({
  authFetch: (url: string, init?: RequestInit) => authFetch(url, init),
}))

function pageResponse(count: number): Response {
  return new Response(new Blob(['png'], { type: 'image/png' }), {
    status: 200,
    headers: { 'X-Page-Count': String(count) },
  })
}

beforeEach(() => {
  authFetch.mockReset().mockImplementation(async () => pageResponse(3))
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:page-${++n}`)
  URL.revokeObjectURL = vi.fn()
})

afterEach(cleanup)

describe('DocumentPages', () => {
  it('shows the first page and how many there are', async () => {
    render(<DocumentPages itemId="statement-stmt_1" title="Card — Sep 2026" />)
    const img = await screen.findByRole('img', { name: 'Card — Sep 2026, page 1 of 3' })
    expect(img.getAttribute('src')).toMatch(/^blob:/)
    expect(authFetch.mock.calls[0]?.[0]).toBe('/api/vault/items/statement-stmt_1/pages/1')
    expect(screen.getByText('Page 1 of 3')).toBeTruthy()
    expect(
      (screen.getByRole('button', { name: 'Previous page' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })

  it('pages forward and back', async () => {
    render(<DocumentPages itemId="file-att_1" title="Policy" />)
    await screen.findByText('Page 1 of 3')
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByRole('img', { name: 'Policy, page 2 of 3' })).toBeTruthy()
    expect(authFetch.mock.calls.at(-1)?.[0]).toBe('/api/vault/items/file-att_1/pages/2')
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(await screen.findByRole('img', { name: 'Policy, page 1 of 3' })).toBeTruthy()
  })

  it('a one-page document has no pager', async () => {
    authFetch.mockImplementation(async () => pageResponse(1))
    render(<DocumentPages itemId="receipt-rcpt_1" title="Receipt" />)
    await screen.findByRole('img', { name: 'Receipt, page 1 of 1' })
    expect(screen.queryByRole('button', { name: 'Next page' })).toBeNull()
  })

  it("says why when a page can't be drawn", async () => {
    authFetch.mockImplementation(
      async () =>
        new Response(JSON.stringify({ detail: 'this PDF can’t be read — download it instead' }), {
          status: 422,
        }),
    )
    render(<DocumentPages itemId="file-att_9" title="Broken" />)
    expect(await screen.findByText(/this PDF can’t be read/)).toBeTruthy()
    expect(screen.queryByRole('img')).toBeNull()
  })

  it('compact: the page is a button that opens the full-size reader', async () => {
    const onExpand = vi.fn()
    render(<DocumentPages itemId="statement-stmt_1" title="Card" onExpand={onExpand} />)
    await screen.findByRole('img', { name: 'Card, page 1 of 3' })
    fireEvent.click(screen.getByRole('button', { name: /Card, page 1 of 3/ }))
    expect(onExpand).toHaveBeenCalledTimes(1)
  })
})
