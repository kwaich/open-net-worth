// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { openShortcutInstall } from './openShortcutInstall'

vi.mock('./pwa', () => ({ isStandalonePwa: vi.fn(() => false) }))
const { isStandalonePwa } = await import('./pwa')

afterEach(() => vi.unstubAllGlobals())

const HREF = '/api/shortcuts/file?t=abc&slug=log-apple-pay'

describe('openShortcutInstall', () => {
  it('opens the file in place in a browser tab', () => {
    const assign = vi.fn()
    vi.stubGlobal('location', { origin: 'https://box.example.com', href: '', assign })
    openShortcutInstall(HREF)
    expect(assign).toHaveBeenCalledWith(HREF)
  })

  it('in the home-screen app, opens the absolute file URL in Safari proper', () => {
    vi.mocked(isStandalonePwa).mockReturnValue(true)
    const loc = { origin: 'https://box.example.com', href: '', assign: vi.fn() }
    vi.stubGlobal('location', loc)
    openShortcutInstall(HREF)
    expect(loc.assign).not.toHaveBeenCalled()
    expect(loc.href).toBe(`x-safari-https://box.example.com${HREF}`)
  })
})
