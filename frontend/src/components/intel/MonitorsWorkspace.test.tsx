// @vitest-environment jsdom

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup, fireEvent, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import type { Watchlist, WatchlistItem, PriceAlert } from './WatchlistsSection'

const mockAuthFetch = vi.fn()
vi.mock('../../api/client', () => ({
  authFetch: (...args: unknown[]) => mockAuthFetch(...args),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => ({ matches: [] }),
}))

import { MonitorsWorkspace } from './MonitorsWorkspace'
import { AssetNavProvider } from '../../state/assetNav'

function Wrapper({ children }: { children: ReactNode }) {
  return <AssetNavProvider>{children}</AssetNavProvider>
}

const DEFAULT_LIST: Watchlist = {
  id: 'default',
  name: 'My holdings',
  description: '',
  is_auto_sync: true,
  kind: 'manual',
  order: 0,
  muted: false,
}

function props(items: WatchlistItem[], alerts: PriceAlert[]) {
  return {
    watchlists: [DEFAULT_LIST],
    items,
    alerts,
    activeListId: 'all',
    onSelectList: vi.fn(),
    tickerMeta: {},
    tickerCcy: {},
    ownedTickers: new Set<string>(),
    luciusCtx: {} as never,
    onEditAlerts: vi.fn(),
    onEditTopic: vi.fn(),
    onRefresh: vi.fn(),
    onClose: vi.fn(),
    onOpenSettings: vi.fn(),
  }
}

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
})

describe('AllMonitorsView — decoupled alerts (ADR 0014)', () => {
  it('finds a monitor and filters alerts currently beyond their price threshold', () => {
    const alerts: PriceAlert[] = [
      { id: 'a1', ticker: 'AAPL', op: '<=', value: 150, note: 'review Apple' },
      { id: 'a2', ticker: 'MSFT', op: '>=', value: 500, note: 'review Microsoft' },
    ]
    const viewProps = {
      ...props([], alerts),
      tickerMeta: { AAPL: { price: 140 }, MSFT: { price: 490 } },
    }
    const { getByRole, baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...viewProps} />
      </Wrapper>,
    )
    fireEvent.click(getByRole('button', { name: 'Triggered now 1' }))
    expect(baseElement.querySelectorAll('.intel-mm-row-sym')).toHaveLength(1)
    expect(baseElement.querySelector('.intel-mm-row-sym')?.textContent).toBe('AAPL')
    fireEvent.change(getByRole('textbox', { name: 'Find a monitor' }), {
      target: { value: 'microsoft' },
    })
    expect(baseElement.querySelector('.intel-mm-empty')?.textContent).toContain('No price alerts')
    fireEvent.click(getByRole('button', { name: 'All 2' }))
    expect(baseElement.querySelector('.intel-mm-row-sym')?.textContent).toBe('MSFT')
  })

  it('renders an alert on a ticker with NO watchlist item (the count-but-never-shown fix)', () => {
    const alerts: PriceAlert[] = [
      { id: 'a1', ticker: 'PLTR', op: '>=', value: 20, note: 'buy zone' },
    ]
    // PLTR is on NO watchlist (items is empty).
    const { baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...props([], alerts)} />
      </Wrapper>,
    )
    // The flat list still renders the ticker row + its alert chip + note…
    expect(baseElement.querySelector('.intel-mm-row-sym')?.textContent).toContain('PLTR')
    expect(baseElement.querySelector('.intel-wlc-alert-chip')?.textContent).toContain('20')
    expect(baseElement.querySelector('.intel-mm-alert-note')?.textContent).toBe('buy zone')
    // …with the "not in any watchlist" affordance (the fix: not silently dropped).
    const none = baseElement.querySelector('.intel-mm-wl-chip--none')
    expect(none?.textContent).toBe('not in any watchlist')
    // Count reflects alert ROWS, not items.
    const alertsSubtab = baseElement.querySelector('.intel-mm-subtab')
    expect(alertsSubtab?.textContent?.replace(/\s+/g, ' ')).toContain('Price alerts (1)')
  })

  it('shows a watchlist-membership chip when the ticker IS on a list', () => {
    const items: WatchlistItem[] = [
      {
        id: 'i1',
        type: 'ticker',
        value: 'AAPL',
        label: 'Apple',
        asset_class: 'equity',
        monitor_news: true,
        added_by: 'user',
        note: '',
        watchlist_ids: ['default'],
      },
    ]
    const alerts: PriceAlert[] = [{ id: 'a1', ticker: 'AAPL', op: '<=', value: 150, note: '' }]
    const { baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...props(items, alerts)} />
      </Wrapper>,
    )
    const chip = baseElement.querySelector('.intel-mm-wl-chip:not(.intel-mm-wl-chip--none)')
    expect(chip?.textContent).toBe('My holdings') // membership chip
    expect(baseElement.querySelector('.intel-mm-wl-chip--none')).toBeNull()
  })
})

describe('MonitorsWorkspace — News sources rail item (INT9 Phase C)', () => {
  it('rail click renders the News sources heading/panel, and the stale-view effect does not bounce it back to All monitors', async () => {
    mockAuthFetch.mockResolvedValue({ ok: true, status: 200, json: async () => ({ sources: [] }) })
    const { getByRole, getByText, baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...props([], [])} />
      </Wrapper>,
    )
    fireEvent.click(getByRole('button', { name: 'News sources' }))

    // MonitorsWorkspace itself renders the heading (NewsSourcesPanel is embedded, so it
    // doesn't duplicate its own) — the rail's fallback effect (watchlists/view changed) must
    // NOT bounce 'sources' back to 'all', which it would if 'sources' weren't exempted.
    expect(baseElement.querySelector('.intel-mm-sources > .intel-mm-list-title')?.textContent).toBe(
      'News sources',
    )
    await waitFor(() => expect(getByText('No sources found')).toBeTruthy())
    // still on the sources view after the panel's own state settles — never reverted to 'all'.
    expect(baseElement.querySelector('.intel-mm-sources')).toBeTruthy()
    expect(baseElement.querySelector('.intel-mm-pane')).toBeNull() // the 'all' view's own wrapper
  })
})

describe('MonitorsWorkspace — Monitoring & alerts', () => {
  it('opens Settings › Intelligence instead of an in-modal view', () => {
    const p = props([], [])
    const { getByRole, baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...p} />
      </Wrapper>,
    )
    fireEvent.click(getByRole('button', { name: 'Monitoring & alerts' }))
    expect(p.onOpenSettings).toHaveBeenCalledTimes(1)
    expect(baseElement.querySelector('.intel-mon-body')).toBeNull()
  })
})
