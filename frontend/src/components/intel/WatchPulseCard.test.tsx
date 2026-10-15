// @vitest-environment jsdom

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { WatchPulse, WatchPulseRow } from '../../types/watchPulse'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

import { WatchPulseCard } from './WatchPulseCard'
import { pulse, pulseRow as row } from './pulseFixtures'
import { _clearWatchPulseCache } from '../../hooks/useWatchPulse'
import { resetPulsePeriodForTests } from '../../hooks/usePulsePeriod'
import { clearQueryCache } from '../../state/queryCache'

function respondWith(body: WatchPulse | null, ok = true) {
  mockAuthFetch.mockResolvedValue({ ok, json: async () => body })
}

const desk = (rows: WatchPulseRow[], unpriced: number) => (
  <div data-testid="desk">
    {rows.map((x) => x.ticker).join(',')}:{unpriced}
  </div>
)

function mount(props: Partial<Parameters<typeof WatchPulseCard>[0]> = {}) {
  return render(
    <WatchPulseCard
      scope={{ id: 'wl_1', name: 'Tech', tickerCount: 1 }}
      ownedRows={[]}
      onAddToWatchlist={vi.fn()}
      renderBody={desk}
      {...props}
    />,
  )
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  _clearWatchPulseCache()
  localStorage.clear()
  resetPulsePeriodForTests()
})
afterEach(cleanup)

describe('WatchPulseCard', () => {
  it('shares the Holdings header — Ask Lucius and the time, no info icon or up/down tally', async () => {
    const onChatSuggest = vi.fn()
    respondWith(pulse())
    const { findByTestId, getByRole, container } = mount({ onChatSuggest })
    await findByTestId('desk')
    fireEvent.click(getByRole('button', { name: 'Ask Lucius about this watchlist' }))
    expect(onChatSuggest).toHaveBeenCalledWith(
      expect.stringMatching(/^Walk me through my watchlist/),
    )
    const head = container.querySelector('.intel-brief-head')!
    expect(head.textContent).toContain('Watchlist pulse')
    expect(head.querySelector('.intel-brief-updated')).toBeTruthy()
    expect(head.querySelector('.info-pop-btn')).toBeNull()
    expect(container.textContent).not.toMatch(/▲|▼|flagged|SPY \+/)
  })

  it('opens with a one-line brief written from the rows', async () => {
    respondWith(pulse())
    const { container } = mount()
    await waitFor(() =>
      expect(container.querySelector('.intel-brief-narrative')?.textContent).toBe(
        'A normal day across the 1 name on Tech; NVDA moved most, +8.0%.',
      ),
    )
  })

  it('hands the desk only this list’s tickers, in attention order, with the unpriced count', async () => {
    const rows = [
      row({ ticker: 'AAA', watchlist_ids: ['wl'], day_pct: 1 }),
      row({ ticker: 'BBB', watchlist_ids: ['wl'], day_pct: -9 }),
      row({ ticker: 'CCC', watchlist_ids: ['other'] }),
    ]
    respondWith(pulse({ rows }))
    const { findByTestId, container } = mount({
      scope: { id: 'wl', name: 'Tech', tickerCount: 3 },
    })
    // Equal scores → the bigger move first; the desk owns the sort menu and column head.
    expect((await findByTestId('desk')).textContent).toBe('BBB,AAA:1')
    expect(container.querySelector('.intel-pulse-colhead')).toBeNull()
    expect(container.querySelector('select')).toBeNull()
  })

  it('merges in the names you own on the list, and the brief names the list', async () => {
    const watched = row({ ticker: 'AAA', watchlist_ids: ['wl'], score: 10 })
    const owned = row({
      ticker: 'MSFT',
      score: 40,
      position: { value: 600, weight: 12, day_pl: 5 },
    })
    respondWith(pulse({ rows: [watched] }))
    const { findByTestId, container } = mount({
      scope: { id: 'wl', name: 'Tech', tickerCount: 2 },
      ownedRows: [owned],
    })
    expect((await findByTestId('desk')).textContent).toBe('MSFT,AAA:0')
    expect(container.querySelector('.intel-brief-narrative')?.textContent).toContain(
      'the 2 names on Tech',
    )
  })

  it('waits for the owned rows instead of counting them as unpriced', async () => {
    respondWith(pulse({ rows: [] }))
    const { container, queryByTestId, rerender } = mount({
      scope: { id: 'wl', name: 'Tech', tickerCount: 2 },
      ownedLoading: true,
    })
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled())
    expect(container.querySelector('[aria-busy="true"]')).toBeTruthy()
    expect(queryByTestId('desk')).toBeNull()
    rerender(
      <WatchPulseCard
        scope={{ id: 'wl', name: 'Tech', tickerCount: 2 }}
        ownedRows={[row({ ticker: 'MSFT' }), row({ ticker: 'AAPL' })]}
        renderBody={desk}
      />,
    )
    expect((await waitFor(() => queryByTestId('desk')!)).textContent).toMatch(/:0$/)
  })

  it('says so when the list has no tickers yet', async () => {
    const onAddToWatchlist = vi.fn()
    respondWith(pulse())
    const { findByText } = mount({
      scope: { id: 'wl-x', name: 'Empty list', tickerCount: 0 },
      onAddToWatchlist,
    })
    expect(await findByText(/“Empty list” has no tickers yet/)).toBeTruthy()
    fireEvent.click(await findByText(/Add a ticker/))
    expect(onAddToWatchlist).toHaveBeenCalled()
  })

  it('still renders the news desk, with the owned names, when prices are unavailable', async () => {
    respondWith(
      pulse({
        status: 'unavailable',
        rows: [],
        summary: { tracked: 2, priced: 0, up: 0, down: 0, flagged: 0 },
      }),
    )
    const { findByTestId, findByText } = mount({
      scope: { id: 'wl', name: 'Tech', tickerCount: 3 },
      ownedRows: [row({ ticker: 'MSFT' })],
    })
    expect(await findByText(/Prices are unavailable right now/)).toBeTruthy()
    expect((await findByTestId('desk')).textContent).toBe('MSFT:2')
  })

  it('keeps the desk when the request fails', async () => {
    mockAuthFetch.mockResolvedValue({ ok: false, json: async () => null })
    const { findByTestId, findByText } = mount()
    expect(await findByText(/Prices are unavailable right now/)).toBeTruthy()
    expect(await findByTestId('desk')).toBeTruthy()
  })
})

describe('WatchPulseCard — verbosity', () => {
  afterEach(() => clearQueryCache())

  it('level 5 shows Lucius’s narrative once it arrives, with its suggestion', async () => {
    mockAuthFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          url.startsWith('/api/intel/watch-brief')
            ? {
                narration: 'NVDA led Tech today.',
                lean: 'neutral',
                lean_reason: 'One name.',
                suggestion: 'Consider waiting for your buy zone.',
              }
            : pulse(),
      }),
    )
    const { findByText } = mount({ verbosity: 5 })
    expect(await findByText('NVDA led Tech today.')).toBeTruthy()
    expect(await findByText('Consider waiting for your buy zone.')).toBeTruthy()
    expect(mockAuthFetch).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/intel\/watch-brief\?list_id=wl_1&period=1M$/),
    )
  })

  it('level 5 falls back to the template when there is no narrative', async () => {
    mockAuthFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        json: async () =>
          url.startsWith('/api/intel/watch-brief') ? { narration: null } : pulse(),
      }),
    )
    const { container } = mount({ verbosity: 5 })
    await waitFor(() =>
      expect(container.querySelector('.intel-brief-narrative')?.textContent).toMatch(
        /NVDA moved most/,
      ),
    )
    expect(container.querySelector('.intel-opinion')).toBeNull()
  })

  it('below level 5 never asks for a narrative', async () => {
    respondWith(pulse())
    const { findByTestId } = mount({ verbosity: 2 })
    await findByTestId('desk')
    expect(mockAuthFetch.mock.calls.some(([u]) => String(u).includes('watch-brief'))).toBe(false)
  })
})
