// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { DocumentReader } from './DocumentReader'

const authFetch = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>()
vi.mock('../../api/client', () => ({
  authFetch: (url: string, init?: RequestInit) => authFetch(url, init),
}))

beforeEach(() => {
  authFetch.mockReset().mockImplementation(
    async () =>
      new Response(new Blob(['png'], { type: 'image/png' }), {
        status: 200,
        headers: { 'X-Page-Count': '3' },
      }),
  )
  let n = 0
  URL.createObjectURL = vi.fn(() => `blob:page-${++n}`)
  URL.revokeObjectURL = vi.fn()
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(cleanup)

describe('DocumentReader', () => {
  it('stacks every page to scroll through, with a pill that jumps between them', async () => {
    render(<DocumentReader itemId="statement-stmt_1" title="Card" />)
    expect(await screen.findByRole('img', { name: 'Card, page 3 of 3' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Card, page 1 of 3' })).toBeTruthy()
    expect(screen.getByRole('img', { name: 'Card, page 2 of 3' })).toBeTruthy()
    const pages = screen.getByRole('group', { name: 'Pages' })
    expect(pages.textContent).toContain('1 / 3')
    expect(
      (screen.getByRole('button', { name: 'Previous page' }) as HTMLButtonElement).disabled,
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({
      behavior: 'smooth',
      block: 'start',
    })
  })

  it('a one-page document has no pill', async () => {
    authFetch.mockImplementation(
      async () =>
        new Response(new Blob(['png']), { status: 200, headers: { 'X-Page-Count': '1' } }),
    )
    render(<DocumentReader itemId="receipt-rcpt_1" title="Receipt" />)
    await screen.findByRole('img', { name: 'Receipt, page 1 of 1' })
    expect(screen.queryByRole('group', { name: 'Pages' })).toBeNull()
  })
})
