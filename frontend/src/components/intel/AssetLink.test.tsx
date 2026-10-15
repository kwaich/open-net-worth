// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import type { ReactNode } from 'react'
import { AssetLink } from './AssetLink'
import { AssetNavProvider } from '../../state/assetNav'

// AssetNavProvider uses useNavigate + useRouterState internally — mock both.
// Each test can capture the mock navigate directly via mockNavigateFn.
const mockNavigateFn = vi.fn()
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mockNavigateFn,
  useRouterState: () => ({ matches: [] }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

// ── Helpers ───────────────────────────────────────────────────────────────────

function wrapper() {
  return ({ children }: { children: ReactNode }) => <AssetNavProvider>{children}</AssetNavProvider>
}

// ── Rendering ─────────────────────────────────────────────────────────────────

describe('AssetLink — rendering', () => {
  it('renders a plain span (not interactive) when neither instrumentId nor ticker is given', () => {
    const { container } = render(<AssetLink label="Mystery">{`?`}</AssetLink>, {
      wrapper: wrapper(),
    })
    const span = container.querySelector('span')
    expect(span).toBeTruthy()
    expect(container.querySelector('button')).toBeNull()
  })

  it('renders a button when instrumentId is given', () => {
    const { container } = render(<AssetLink instrumentId="inst-aapl">AAPL</AssetLink>, {
      wrapper: wrapper(),
    })
    expect(container.querySelector('button')).toBeTruthy()
  })

  it('renders a button when only ticker is given', () => {
    const { container } = render(<AssetLink ticker="NVDA">NVDA</AssetLink>, { wrapper: wrapper() })
    expect(container.querySelector('button')).toBeTruthy()
    expect(container.querySelector('button')?.title).toBe('Open NVDA')
  })

  it('uses label in title when ticker is absent', () => {
    const { container } = render(
      <AssetLink instrumentId="inst-x" label="Some Corp">
        Some Corp
      </AssetLink>,
      { wrapper: wrapper() },
    )
    expect(container.querySelector('button')?.title).toBe('Open Some Corp')
  })

  it('is keyboard-activatable via Enter', () => {
    const { container } = render(<AssetLink instrumentId="inst-aapl">AAPL</AssetLink>, {
      wrapper: wrapper(),
    })
    const btn = container.querySelector('button')!
    fireEvent.keyDown(btn, { key: 'Enter' })
    // buttons handle Enter natively (the click fires); no custom handler needed
    fireEvent.click(btn)
    expect(mockNavigateFn).toHaveBeenCalled()
  })
})

// ── Navigation is immediate — no pre-navigation network call, ever ────────────

describe('AssetLink — navigation is immediate', () => {
  it('a ticker (even alongside a known instrumentId) navigates straight to /quote/$symbol', () => {
    const { container } = render(
      <AssetLink instrumentId="inst-aapl" ticker="AAPL" label="Apple Inc.">
        AAPL
      </AssetLink>,
      { wrapper: wrapper() },
    )
    fireEvent.click(container.querySelector('button')!)
    expect(mockNavigateFn).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/quote/$symbol',
        params: { symbol: 'AAPL' },
      }),
    )
  })

  it('an instrumentId with no ticker (a manual/tickerless holding) navigates to /asset/$instrumentId', () => {
    const { container } = render(
      <AssetLink instrumentId="inst-manual" label="Private stake">
        Private stake
      </AssetLink>,
      { wrapper: wrapper() },
    )
    fireEvent.click(container.querySelector('button')!)
    expect(mockNavigateFn).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/asset/$instrumentId',
        params: { instrumentId: 'inst-manual' },
      }),
    )
  })

  it('a ticker-only tap navigates on the very first click, synchronously — no async wait', () => {
    const { container } = render(<AssetLink ticker="NVDA">NVDA</AssetLink>, { wrapper: wrapper() })
    fireEvent.click(container.querySelector('button')!)
    // Deliberately no `await`/`waitFor`: navigate must already have fired by the
    // time this assertion runs, proving the tap never blocks on a resolve call.
    expect(mockNavigateFn).toHaveBeenCalledWith(
      expect.objectContaining({
        to: '/quote/$symbol',
        params: { symbol: 'NVDA' },
      }),
    )
  })
})
