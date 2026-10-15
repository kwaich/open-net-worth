// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent } from '@testing-library/react'
import { QuoteDetailView } from './QuoteDetailView'

const mockUseResolveTicker = vi.fn()
vi.mock('../../hooks/useResolveTicker', () => ({
  useResolveTicker: (ticker: string) => mockUseResolveTicker(ticker),
}))

// AssetDetailView pulls in a large tree (chart, news, position, etc.) — out of scope
// here; QuoteDetailView's own job is just picking which of the three states to render.
vi.mock('./AssetDetailView', () => ({
  AssetDetailView: ({
    instrumentId,
    seedTicker,
  }: {
    instrumentId: string
    seedTicker?: string
  }) => (
    <div data-testid="asset-detail-view">
      {instrumentId}:{seedTicker}
    </div>
  ),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('QuoteDetailView', () => {
  it('shows a skeleton while resolving', () => {
    mockUseResolveTicker.mockReturnValue({
      instrumentId: null,
      resolving: true,
      error: false,
      retry: vi.fn(),
    })
    const { container } = render(<QuoteDetailView ticker="ONWAI.DE" seedName="ONWAI Inc" />)
    expect(container.querySelector('[role="status"]')).toBeTruthy()
    expect(container.querySelector('[data-testid="asset-detail-view"]')).toBeNull()
  })

  it('shows an error state with a retry action when resolution fails', () => {
    const retry = vi.fn()
    mockUseResolveTicker.mockReturnValue({
      instrumentId: null,
      resolving: false,
      error: true,
      retry,
    })
    const { getByText } = render(<QuoteDetailView ticker="XYZJUNK" />)
    expect(getByText(/Could not find a market instrument for "XYZJUNK"/i)).toBeTruthy()
    fireEvent.click(getByText(/retry/i))
    expect(retry).toHaveBeenCalled()
  })

  it('renders AssetDetailView with the resolved instrument id once resolution succeeds', () => {
    mockUseResolveTicker.mockReturnValue({
      instrumentId: 'inst-onwai',
      resolving: false,
      error: false,
      retry: vi.fn(),
    })
    const { getByTestId } = render(<QuoteDetailView ticker="ONWAI.DE" />)
    expect(getByTestId('asset-detail-view').textContent).toBe('inst-onwai:ONWAI.DE')
  })
})
