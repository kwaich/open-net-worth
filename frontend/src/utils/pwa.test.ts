// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { stampPwaDisplayMode } from './pwa'

const originalMatchMedia = window.matchMedia
const originalStandalone = (navigator as Navigator & { standalone?: boolean }).standalone

afterEach(() => {
  window.matchMedia = originalMatchMedia
  Object.defineProperty(navigator, 'standalone', {
    configurable: true,
    value: originalStandalone,
  })
  delete document.documentElement.dataset.onwDisplayMode
})

describe('stampPwaDisplayMode', () => {
  it('stamps an ordinary browser tab before shell CSS chooses its viewport', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: false })
    Object.defineProperty(navigator, 'standalone', { configurable: true, value: false })

    stampPwaDisplayMode()

    expect(document.documentElement.dataset.onwDisplayMode).toBe('browser')
  })

  it('trusts the iOS standalone flag when the display-mode query misreports browser mode', () => {
    window.matchMedia = vi.fn().mockReturnValue({ matches: false })
    Object.defineProperty(navigator, 'standalone', { configurable: true, value: true })

    stampPwaDisplayMode()

    expect(document.documentElement.dataset.onwDisplayMode).toBe('standalone')
  })
})
