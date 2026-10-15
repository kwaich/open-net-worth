// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { NewVersionBanner } from './NewVersionBanner'

const shell = (entry: string) =>
  `<!doctype html><html><head><script type="module" crossorigin src="${entry}"></script></head></html>`

beforeEach(() => {
  const script = document.createElement('script')
  script.type = 'module'
  script.src = '/assets/index-OLD111.js'
  document.head.appendChild(script)
})
afterEach(() => {
  cleanup()
  document.head.querySelectorAll('script').forEach((s) => s.remove())
  vi.restoreAllMocks()
})

async function returnToApp() {
  await act(async () => {
    document.dispatchEvent(new Event('visibilitychange'))
    await new Promise((r) => setTimeout(r, 0))
  })
}

describe('NewVersionBanner', () => {
  it('offers a reload once the server serves a different build', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(shell('/assets/index-NEW222.js')))
    render(<NewVersionBanner />)
    expect(screen.queryByRole('status')).toBeNull()
    await returnToApp()
    expect(screen.getByRole('status').textContent).toMatch(/new version is ready/)
    expect(screen.getByRole('button', { name: 'Reload' })).toBeTruthy()
    expect(window.fetch).toHaveBeenCalledWith('/', expect.objectContaining({ cache: 'no-store' }))
  })

  it('tapping Reload turns the button into a spinner at once', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValue(new Response(shell('/assets/index-NEW222.js')))
    const raf = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 0)
    render(<NewVersionBanner />)
    await returnToApp()
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }))
    const busy = screen.getByRole('button', { name: 'Reloading' }) as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    expect(busy.querySelector('.ui-btn__spinner')).toBeTruthy()
    expect(raf).toHaveBeenCalled() // the reload itself waits a frame, for the spinner to show
  })

  it('stays quiet while the running build is current, or the check fails', async () => {
    const fetch = vi.spyOn(window, 'fetch')
    fetch.mockResolvedValueOnce(new Response(shell('/assets/index-OLD111.js')))
    render(<NewVersionBanner />)
    await returnToApp()
    fetch.mockRejectedValueOnce(new Error('offline'))
    await returnToApp()
    expect(screen.queryByRole('status')).toBeNull()
  })
})
