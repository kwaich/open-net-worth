// @vitest-environment jsdom

// Regression: before PortfolioProvider existed, RouterShell, Intel, and useLuciusContext each
// called usePortfolio() independently — every mounted consumer ran its own fetches plus its own
// uncoordinated 60s refresh timer. This asserts N consumers sharing ONE PortfolioProvider
// produce exactly ONE fetch, not one per consumer.
//
// The endpoint set collapsed from three to one when the valuation moved server-side
// (ADR 2026-07-31-overview-aggregates-are-server-computed): the hook used to fetch
// /api/portfolio + /api/accounts + /api/prices and fold them itself — the last being a real,
// slow Yahoo/CoinGecko round trip made from the browser. It now reads /api/portfolio/rows,
// which arrives already valued, so the price fetch happens once on the server instead.

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor } from '@testing-library/react'

const mockAuthFetch = vi.fn()
vi.mock('../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { PortfolioProvider, usePortfolioContext } from './portfolio'
import { clearQueryCache } from './queryCache'

function okResponse(body: unknown): Promise<Response> {
  return Promise.resolve({ ok: true, json: () => Promise.resolve(body) } as Response)
}

function urlOf(input: unknown): string {
  return typeof input === 'string' ? input : String(input)
}

function Consumer() {
  usePortfolioContext()
  return null
}

describe('PortfolioProvider', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockAuthFetch.mockImplementation((input: unknown) => {
      const url = urlOf(input)
      if (url.startsWith('/api/portfolio')) {
        return okResponse({
          base_currency: 'USD',
          rows: [],
          portfolio_total: 0,
          unvested_base: 0,
          holdings_by_account: {},
          is_live: true,
          fx: {},
        })
      }
      return okResponse({})
    })
  })

  it('fetches the portfolio once no matter how many consumers mount', async () => {
    // Asserts the INVARIANT — sharing a provider means the consumer count does not multiply the
    // requests — rather than a fixed number, because how many stages the load uses is
    // `usePortfolio`'s own business (it currently paints from `?live=0` before the live read).
    const portfolioCalls = () =>
      mockAuthFetch.mock.calls.filter(([u]) => urlOf(u).startsWith('/api/portfolio')).length

    const one = render(
      <PortfolioProvider>
        <Consumer />
      </PortfolioProvider>,
    )
    await waitFor(() => expect(portfolioCalls()).toBeGreaterThan(0))
    const withOneConsumer = portfolioCalls()
    one.unmount()

    mockAuthFetch.mockClear()
    clearQueryCache()

    render(
      <PortfolioProvider>
        <Consumer />
        <Consumer />
        <Consumer />
      </PortfolioProvider>,
    )
    await waitFor(() => expect(portfolioCalls()).toBeGreaterThan(0))
    await waitFor(() => expect(portfolioCalls()).toBe(withOneConsumer))

    // Three consumers, same cost as one — and the browser never calls the price vendor: the
    // server values the rows now.
    expect(portfolioCalls()).toBe(withOneConsumer)
    expect(
      mockAuthFetch.mock.calls.filter(([u]) => urlOf(u).startsWith('/api/prices')),
    ).toHaveLength(0)
  })
})
