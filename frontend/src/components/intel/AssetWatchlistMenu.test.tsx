// @vitest-environment jsdom

import { describe, it, expect, afterEach, beforeEach, vi } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import { AssetWatchlistMenu } from './AssetWatchlistMenu'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))

// NVDA is in "Core holdings" only; AAPL is in both lists. So for NVDA: badge = 1,
// counts core=2 / ai=1.
const WL_PAYLOAD = {
  watchlists: [
    { id: 'core', name: 'Core holdings', order: 0 },
    { id: 'ai', name: 'AI & semis', order: 1 },
  ],
  items: [
    { id: 'item-nvda', type: 'ticker', value: 'NVDA', watchlist_ids: ['core'] },
    { id: 'item-aapl', type: 'ticker', value: 'AAPL', watchlist_ids: ['core', 'ai'] },
  ],
}

function routeFetch() {
  mockAuthFetch.mockImplementation((url: string, opts?: { method?: string }) => {
    const method = opts?.method ?? 'GET'
    if (url === '/api/watchlist' && method === 'GET')
      return Promise.resolve({ ok: true, json: async () => WL_PAYLOAD })
    if (url === '/api/watchlist/items' && method === 'POST')
      return Promise.resolve({
        ok: true,
        status: 201,
        json: async () => ({ id: 'item-nvda', value: 'NVDA' }),
      })
    if (url.startsWith('/api/watchlist/items/') && method === 'DELETE')
      return Promise.resolve({ ok: true, json: async () => ({ ok: true }) })
    if (url === '/api/watchlists' && method === 'POST')
      return Promise.resolve({
        ok: true,
        json: async () => ({ id: 'new-1', name: 'New watchlist' }),
      })
    return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
  })
}

function deleteCalls() {
  return mockAuthFetch.mock.calls.filter(([, o]) => o?.method === 'DELETE')
}
function postCalls(url: string) {
  return mockAuthFetch.mock.calls.filter(([u, o]) => u === url && o?.method === 'POST')
}

afterEach(cleanup)
beforeEach(() => {
  mockAuthFetch.mockReset()
  routeFetch()
})

async function openMenu(getByText: (t: RegExp | string) => HTMLElement) {
  // Wait for the mount fetch to settle (badge "1" proves membership loaded), then open.
  await waitFor(() => expect(getByText('1')).toBeTruthy())
  fireEvent.click(getByText('Watchlists'))
  await waitFor(() => expect(getByText('Core holdings')).toBeTruthy())
}

describe('AssetWatchlistMenu', () => {
  // ADWL-COUNT-BADGE + ADWL-OPEN
  it('fetches membership on mount, shows the count badge, and lists all watchlists with member state', async () => {
    const { getByText, getByRole } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={vi.fn()} />,
    )
    await openMenu(getByText)
    expect(mockAuthFetch).toHaveBeenCalledWith('/api/watchlist')
    expect(
      getByRole('menuitemcheckbox', { name: /Core holdings/ }).getAttribute('aria-checked'),
    ).toBe('true')
    expect(getByRole('menuitemcheckbox', { name: /AI & semis/ }).getAttribute('aria-checked')).toBe(
      'false',
    )
  })

  // ADWL-ADD — adding targets ONLY that list (watchlist: <id>), never a global add.
  it('adds the ticker to a single list via POST with watchlist=<listId>', async () => {
    const { getByText, getByRole } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={vi.fn()} />,
    )
    await openMenu(getByText)
    fireEvent.click(getByRole('menuitemcheckbox', { name: /AI & semis/ }))
    await waitFor(() => expect(postCalls('/api/watchlist/items').length).toBe(1))
    const [, opts] = postCalls('/api/watchlist/items')[0]!
    expect(JSON.parse(opts.body)).toMatchObject({ type: 'ticker', value: 'NVDA', watchlist: 'ai' })
  })

  // ADWL-REMOVE-NONDESTRUCTIVE — remove scopes to one list (?watchlist_id=), not a bare delete.
  it('removes the ticker from one list only via DELETE ?watchlist_id=<listId>', async () => {
    const { getByText, getByRole } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={vi.fn()} />,
    )
    await openMenu(getByText)
    fireEvent.click(getByRole('menuitemcheckbox', { name: /Core holdings/ }))
    await waitFor(() => expect(deleteCalls().length).toBe(1))
    expect(deleteCalls()[0]![0]).toBe('/api/watchlist/items/item-nvda?watchlist_id=core')
  })

  // ADWL-NEW — creates a list then adds the ticker to it.
  it('creates a new watchlist and adds the ticker to it', async () => {
    const { getByText } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={vi.fn()} />,
    )
    await openMenu(getByText)
    fireEvent.click(getByText('New watchlist'))
    await waitFor(() => expect(postCalls('/api/watchlists').length).toBe(1))
    await waitFor(() => {
      const add = postCalls('/api/watchlist/items')
      expect(add.length).toBe(1)
      expect(JSON.parse(add[0]![1].body)).toMatchObject({ watchlist: 'new-1' })
    })
  })

  // ADWL-CLOSE — a GET-only refetch fires on close, but only after a real change.
  it('calls onChanged on close after a membership change', async () => {
    const onChanged = vi.fn()
    const { getByText, getByRole, container } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={onChanged} />,
    )
    await openMenu(getByText)
    fireEvent.click(getByRole('menuitemcheckbox', { name: /AI & semis/ }))
    await waitFor(() => expect(postCalls('/api/watchlist/items').length).toBe(1))
    const overlay = container.querySelector('.asd-wl-overlay') as HTMLElement
    fireEvent.click(overlay)
    expect(onChanged).toHaveBeenCalledTimes(1)
  })

  it('does NOT call onChanged when closed without any change', async () => {
    const onChanged = vi.fn()
    const { getByText, container } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={onChanged} />,
    )
    await openMenu(getByText)
    const overlay = container.querySelector('.asd-wl-overlay') as HTMLElement
    fireEvent.click(overlay)
    expect(onChanged).not.toHaveBeenCalled()
  })
})

// ADWL-AUTO-SYNC — My holdings row is disabled and non-togglable (ADR 0018).
describe('AssetWatchlistMenu — auto-sync list is read-only', () => {
  const WL_WITH_AUTO_SYNC = {
    watchlists: [
      { id: 'my_holdings', name: 'My holdings', order: 0, is_auto_sync: true },
      { id: 'ai', name: 'AI & semis', order: 1, is_auto_sync: false },
    ],
    items: [{ id: 'item-nvda', type: 'ticker', value: 'NVDA', watchlist_ids: ['my_holdings'] }],
  }

  beforeEach(() => {
    mockAuthFetch.mockImplementation((url: string, opts?: { method?: string }) => {
      const method = opts?.method ?? 'GET'
      if (url === '/api/watchlist' && method === 'GET')
        return Promise.resolve({ ok: true, json: async () => WL_WITH_AUTO_SYNC })
      return Promise.resolve({ ok: false, status: 404, json: async () => ({}) })
    })
  })

  it('disables the My holdings row and does not fire toggle on click', async () => {
    const { getByText, getByRole } = render(
      <AssetWatchlistMenu ticker="NVDA" assetClass="equity" onChanged={vi.fn()} />,
    )
    // Badge shows 1 (member of my_holdings) — wait for load.
    await waitFor(() => expect(getByText('1')).toBeTruthy())
    fireEvent.click(getByText('Watchlists'))
    await waitFor(() => expect(getByText('My holdings')).toBeTruthy())

    const holdingsRow = getByRole('menuitemcheckbox', { name: /My holdings/ })
    expect((holdingsRow as HTMLButtonElement).disabled).toBe(true)
    expect(holdingsRow.getAttribute('title')).toContain('Auto-managed')

    // Clicking the disabled row must not fire any API call.
    fireEvent.click(holdingsRow)
    expect(
      mockAuthFetch.mock.calls.filter(([, o]) => o?.method === 'POST' || o?.method === 'DELETE'),
    ).toHaveLength(0)
  })
})
