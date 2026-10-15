// @vitest-environment jsdom
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest'

// router.tsx runs its redirect logic inside beforeLoad, which we can't easily unit-test through
// TanStack Router's full machinery without a browser. What we CAN and must verify directly is
// the pure decision the redirect makes: given a saved tab (or none) and a viewport width, which
// tab id does it pick? That logic is extracted into `defaultLandingTab` below so this test
// exercises the real function, not a re-implementation of it.
import { defaultLandingTab } from './router'

function mockViewport(widthPx: number) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('639.98') ? widthPx <= 639.98 : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

describe('defaultLandingTab', () => {
  beforeEach(() => {
    localStorage.clear()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('defaults to lucius on desktop with no saved tab', () => {
    mockViewport(1400)
    expect(defaultLandingTab(null)).toBe('lucius')
  })

  it('defaults to dashboard on a phone-width viewport with no saved tab', () => {
    mockViewport(390)
    expect(defaultLandingTab(null)).toBe('dashboard')
  })

  it('an invalid saved tab falls back the same way a missing one does', () => {
    mockViewport(1400)
    expect(defaultLandingTab('not-a-real-tab')).toBe('lucius')
  })

  it('a valid saved tab always wins, on any viewport', () => {
    mockViewport(390)
    expect(defaultLandingTab('investments')).toBe('investments')
    mockViewport(1400)
    expect(defaultLandingTab('investments')).toBe('investments')
  })
})
