/**
 * @vitest-environment jsdom
 *
 * The market half of the status-bar ticker (gold, coins, FX, mega-caps) is context for someone
 * who invests. A book of cash and cards (persona QA round 5) watched Gold, BTC and NVDA scroll
 * past with no tie to its own money. It now leads only with a market position or a watchlist,
 * or when switched on in Settings, and when hidden it is not even polled.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import type { ComputedRow } from '../types/portfolio'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

const useMarketTicker = vi.hoisted(() => vi.fn())
vi.mock('../hooks/useMarketTicker', () => ({ useMarketTicker }))

const fetchJson = vi.hoisted(() => vi.fn())
vi.mock('../hooks/serverState', () => ({ fetchJson }))

import { TickerStrip } from './TickerStrip'
import { setTickerMarketAlways, TICKER_MARKET_STORAGE_KEY } from '../tickerPref'

const GOLD = {
  symbol: 'GC=F',
  label: 'Gold',
  kind: 'commodity',
  unit: 'USD',
  price: 4691.5,
  change_pct: 0.2,
}

function held(symbol: string): ComputedRow {
  return {
    symbol,
    _base: 1000,
    _disp_unit_price: 146.47,
    _disp_ccy: 'USD',
    _daily_plpct: 0.4,
  } as unknown as ComputedRow
}

function renderStrip(rows: ComputedRow[]) {
  return render(<TickerStrip rows={rows} priceStatus="live" asOf="04:07 PM" asOfLong="As of now" />)
}

beforeEach(() => {
  cleanup()
  vi.clearAllMocks()
  localStorage.removeItem(TICKER_MARKET_STORAGE_KEY)
  useMarketTicker.mockImplementation((enabled: boolean) => (enabled ? [GOLD] : []))
  fetchJson.mockImplementation(async () => ({ items: [] }))
})

afterEach(() => cleanup())

describe('the ticker market half', () => {
  it('stays hidden, and unpolled, for a book with no market positions and no watchlist', async () => {
    renderStrip([])

    await waitFor(() => expect(fetchJson).toHaveBeenCalledWith('/api/watchlist'))
    expect(useMarketTicker).toHaveBeenLastCalledWith(false)
    expect(screen.queryByText('Gold')).toBeNull()
  })

  it('shows with a market position, without asking for the watchlist', () => {
    renderStrip([held('ORCL')])

    expect(useMarketTicker).toHaveBeenLastCalledWith(true)
    expect(screen.getAllByText('Gold').length).toBeGreaterThan(0)
    expect(fetchJson).not.toHaveBeenCalled()
  })

  it('shows once the user follows a ticker', async () => {
    fetchJson.mockImplementation(async () => ({ items: [{ type: 'ticker' }] }))
    renderStrip([])

    await waitFor(() => expect(screen.getAllByText('Gold').length).toBeGreaterThan(0))
  })

  it('a topic on the watchlist is not a ticker to follow', async () => {
    fetchJson.mockImplementation(async () => ({ items: [{ type: 'topic' }] }))
    renderStrip([])

    await waitFor(() => expect(fetchJson).toHaveBeenCalled())
    expect(screen.queryByText('Gold')).toBeNull()
  })

  it('the Settings switch turns it on regardless, and back off', async () => {
    renderStrip([])
    await waitFor(() => expect(fetchJson).toHaveBeenCalled())
    expect(screen.queryByText('Gold')).toBeNull()

    setTickerMarketAlways(true)
    await waitFor(() => expect(screen.getAllByText('Gold').length).toBeGreaterThan(0))

    setTickerMarketAlways(false)
    await waitFor(() => expect(screen.queryByText('Gold')).toBeNull())
  })
})
