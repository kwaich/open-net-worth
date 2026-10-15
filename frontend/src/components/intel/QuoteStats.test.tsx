// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, waitFor } from '@testing-library/react'
import { fDate } from '../../utils/format'
import { QuoteStats } from './QuoteStats'
import { _clearQuoteStatsCache } from './quoteStatsCache'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
  _clearQuoteStatsCache()
})

function respond(body: unknown) {
  mockAuthFetch.mockResolvedValue({ ok: true, json: () => Promise.resolve(body) })
}

describe('QuoteStats', () => {
  it('renders the equity row set for kind=equity (pre-CHART3 behavior)', async () => {
    respond({
      currency: 'USD',
      kind: 'equity',
      stats: { prev_close: 314.86, pe_ratio: 38.17, market_cap: 4_810_000_000_000 },
    })
    render(<QuoteStats instrumentId="i-aapl" currency="USD" />)
    await waitFor(() => expect(screen.getByText('P/E Ratio')).toBeDefined())
    expect(screen.getByText('38.17')).toBeDefined()
    expect(screen.getByText('$4.81T')).toBeDefined()
    // Crypto-only rows never leak into the equity strip.
    expect(screen.queryByText('Circulating Supply')).toBeNull()
    expect(screen.queryByText('Start Date')).toBeNull()
  })

  it('renders the crypto row set with supplies and start date for kind=crypto', async () => {
    respond({
      currency: 'SGD',
      kind: 'crypto',
      stats: {
        market_cap: 1_720_000_000_000,
        fdv: 1_810_000_000_000,
        volume_24h: 38_800_000_000,
        vol_to_mcap: 0.0233,
        day_low: 85_600,
        day_high: 86_900,
        ath: 169_000,
        atl: 230,
        circulating_supply: 20_060_000,
        total_supply: 20_060_000,
        max_supply: 21_000_000,
        genesis_date: '2009-01-03',
      },
    })
    render(<QuoteStats instrumentId="i-btc" currency="SGD" />)
    await waitFor(() => expect(screen.getByText('Circulating Supply')).toBeDefined())
    expect(screen.getByText('Market Cap')).toBeDefined()
    expect(screen.getByText('Fully Diluted Val.')).toBeDefined()
    expect(screen.getByText('Vol/Market Cap (24h)')).toBeDefined()
    expect(screen.getByText('2.33%')).toBeDefined()
    expect(screen.getByText('21.00M')).toBeDefined()
    expect(screen.getByText(fDate('2009-01-03', 'short', 'always'))).toBeDefined()
    // Equity-only rows never leak into the crypto strip.
    expect(screen.queryByText('P/E Ratio')).toBeNull()
    expect(screen.queryByText('Dividend Yield')).toBeNull()
  })

  it('formats crypto money in the RESPONSE currency, trusting a USD fallback over the prop', async () => {
    respond({
      currency: 'USD',
      kind: 'crypto',
      stats: { market_cap: 1_284_000_000_000, circulating_supply: 20_060_000 },
    })
    render(<QuoteStats instrumentId="i-btc" currency="VND" />)
    await waitFor(() => expect(screen.getByText('Market Cap')).toBeDefined())
    expect(screen.getByText('$1.28T')).toBeDefined() // USD symbol, not the VND prop
  })

  it('renders nothing when stats are empty', async () => {
    respond({ currency: 'SGD', kind: 'crypto', stats: {} })
    const { container } = render(<QuoteStats instrumentId="i-btc" currency="SGD" />)
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('.asd-stats')).toBeNull())
  })

  it('is on the page from the first paint, as a skeleton, until the stats land', async () => {
    // Owner-reproduced 2026-09-21: "the stats grid below the chart is not shown initially,
    // only after the data is loaded" — the section popped in late and everything under it
    // (Lucius's take, the movement timeline) jumped down. Same shape from first paint.
    let resolve!: (v: unknown) => void
    mockAuthFetch.mockReturnValue(new Promise((r) => (resolve = r)))
    const { container } = render(<QuoteStats instrumentId="i-msft" currency="USD" kind="equity" />)
    expect(container.querySelector('.asd-stats')).not.toBeNull()
    expect(container.querySelectorAll('.asd-stat').length).toBe(9)
    expect(container.querySelectorAll('.ui-skeleton').length).toBeGreaterThan(0)
    expect(screen.getByText('Prev Close')).toBeTruthy()
    resolve({
      ok: true,
      json: () =>
        Promise.resolve({ currency: 'USD', kind: 'equity', stats: { prev_close: 493.78 } }),
    })
    await screen.findByText('$493.78')
    expect(container.querySelectorAll('.ui-skeleton').length).toBe(0)
  })

  it('a crypto placeholder carries the crypto labels, not the equity ones', () => {
    mockAuthFetch.mockReturnValue(new Promise(() => {}))
    render(<QuoteStats instrumentId="i-btc" currency="USD" kind="crypto" />)
    expect(screen.getByText('Circulating Supply')).toBeTruthy()
    expect(screen.queryByText('P/E Ratio')).toBeNull()
  })

  it('reopens instantly from the session cache, then revalidates', async () => {
    respond({ currency: 'USD', kind: 'equity', stats: { prev_close: 493.78 } })
    const first = render(<QuoteStats instrumentId="i-msft" currency="USD" kind="equity" />)
    await screen.findByText('$493.78')
    first.unmount()
    mockAuthFetch.mockReturnValue(new Promise(() => {}))
    const { container } = render(<QuoteStats instrumentId="i-msft" currency="USD" kind="equity" />)
    expect(screen.getByText('$493.78')).toBeTruthy()
    expect(container.querySelectorAll('.ui-skeleton').length).toBe(0)
  })
})
