// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { AutomationTrigger } from '../../../api/automations'
import { AutomationTriggerFields } from './AutomationTriggerFields'
import { automationTriggerLabel } from './AutomationTriggerSummary'

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const SEARCH = [
  {
    symbol: 'D05.SI',
    name: 'DBS Group',
    asset_class: 'equity',
    exchange: 'SES',
    price_source: 'yahoo',
    price_source_id: 'D05.SI',
  },
]

const QUOTE = {
  prices: { 'D05.SI': 41.2 },
  prev_prices: { 'D05.SI': 41.0 },
  names: { 'D05.SI': 'DBS Group Holdings Ltd' },
  exchanges: { 'D05.SI': 'SES' },
  currencies: { 'D05.SI': 'SGD' },
}

function json(body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }))
}

function price(when: Record<string, unknown>): AutomationTrigger {
  return { kind: 'event', event: 'price', when, limit: 'day' }
}

describe('price trigger ticker', () => {
  it('searches the whole market, resolves the pick and shows its quote currency', async () => {
    const fetchMock = vi.fn((url: string) =>
      url.startsWith('/api/intel/assets/search')
        ? json(SEARCH)
        : url.startsWith('/api/prices')
          ? json(QUOTE)
          : json({ instrument_id: 'inst_dbs', ticker: 'D05.SI', currency: 'SGD' }),
    )
    vi.stubGlobal('fetch', fetchMock)
    const onChange = vi.fn()
    const { rerender } = render(
      <AutomationTriggerFields
        trigger={price({ symbol: 'd05', direction: 'above', value: 40 })}
        options={{ accounts: [], instruments: [] }}
        onChange={onChange}
      />,
    )
    const ticker = screen.getByRole('combobox', { name: /Ticker/ })
    fireEvent.focus(ticker)
    const option = await screen.findByRole('option', { name: /D05\.SI.*DBS Group/ })
    await act(async () => {
      fireEvent.mouseDown(option)
    })
    await waitFor(() =>
      expect(onChange).toHaveBeenLastCalledWith(
        price({ symbol: 'D05.SI', direction: 'above', value: 40 }),
        'trigger.when.symbol',
      ),
    )
    const resolveCall = fetchMock.mock.calls.find(([url]) => url === '/api/intel/assets/resolve')
    const init = (resolveCall as unknown as [string, RequestInit])[1]
    expect(JSON.parse(String(init.body))).toMatchObject({
      q: 'D05.SI',
      price_source: 'yahoo',
      price_source_id: 'D05.SI',
    })
    rerender(
      <AutomationTriggerFields
        trigger={price({ symbol: 'D05.SI', direction: 'above', value: 40 })}
        options={{ accounts: [], instruments: [] }}
        onChange={onChange}
      />,
    )
    expect(screen.getByText('SGD')).toBeTruthy()
  })

  it('confirms what the ticker is — name, exchange, currency and today’s price', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json(QUOTE)),
    )
    const onChange = vi.fn()
    render(
      <AutomationTriggerFields
        trigger={price({ symbol: 'D05.SI', direction: 'above', value: 40 })}
        options={{ accounts: [], instruments: [] }}
        onChange={onChange}
      />,
    )
    expect(await screen.findByText('DBS Group Holdings Ltd', {}, { timeout: 3000 })).toBeTruthy()
    expect(screen.getByText(/D05\.SI · SES · SGD · last SGD 41\.2 \(\+0\.5%\)/)).toBeTruthy()
    // The threshold takes the currency the quote reported.
    expect(screen.getAllByText('SGD').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: 'Not this one? Search again' }))
    expect(onChange).toHaveBeenLastCalledWith(
      price({ symbol: '', direction: 'above', value: 40 }),
      'trigger.when.symbol',
    )
  })

  it('says so when the ticker is not on the market', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => json({ prices: {}, names: {}, currencies: {} })),
    )
    render(
      <AutomationTriggerFields
        trigger={price({ symbol: 'ZZNOPE', direction: 'above', value: 1 })}
        options={{ accounts: [], instruments: [] }}
        onChange={vi.fn()}
      />,
    )
    expect(
      await screen.findByText(/Couldn’t find ZZNOPE on the market/, {}, { timeout: 3000 }),
    ).toBeTruthy()
  })

  it('the card names the ticker’s own currency — never a hard-coded one', () => {
    const options = {
      accounts: [],
      instruments: [
        { id: 'inst_dbs', label: 'D05.SI', currency: 'SGD' },
        { id: 'inst_aapl', label: 'AAPL', currency: 'USD' },
      ],
    }
    expect(
      automationTriggerLabel(price({ symbol: 'inst_dbs', direction: 'below', value: 35 }), options),
    ).toBe('When D05.SI falls below SGD 35 • at most once a day')
    expect(
      automationTriggerLabel(price({ symbol: 'AAPL', direction: 'above', value: 180 }), options),
    ).toBe('When AAPL rises above USD 180 • at most once a day')
    // A ticker we know nothing about yet: no currency rather than a wrong one.
    expect(
      automationTriggerLabel(price({ symbol: 'ZZNEW', direction: 'above', value: 5 }), options),
    ).toBe('When ZZNEW rises above 5 • at most once a day')
  })
})
