// @vitest-environment jsdom
//
// `watchlist` and `alert` are registered Lucius mention types (descriptors/store_backed.py
// order=11, descriptors/new_types.py order=18) that emitted no data-entity-id, so neither
// could be dragged into the dock or right-clicked "Ask Lucius about this" (owner, 2026-08-23).
//
// The alert wiring is CONDITIONAL on `a.id` (optional on the client PriceAlert type), so the
// negative case is tested too: an id-less alert must render exactly as before rather than
// emit an unresolvable ref. dragRightClick binds globally on [data-entity-id][draggable].

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'
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
  id: 'wl_core',
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
    onChatSuggest: vi.fn(),
    onClose: vi.fn(),
    onOpenSettings: vi.fn(),
  }
}

afterEach(() => {
  cleanup()
  mockAuthFetch.mockReset()
})

describe('Intel Lucius wiring', () => {
  it('wires each watchlist in the rail so drag + Ask Lucius work', () => {
    const { baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...props([], [])} />
      </Wrapper>,
    )

    const row = baseElement.querySelector<HTMLElement>('[data-entity-id="watchlist:wl_core"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('data-entity-label')).toBe('My holdings')
    expect(row!.getAttribute('draggable')).toBe('true')
    expect(row!.getAttribute('data-ctx-type')).toBe('watchlist')
  })

  it('wires a price alert that has an id', () => {
    const alerts: PriceAlert[] = [
      { id: 'al_123', ticker: 'PLTR', op: '>=', value: 20, note: 'buy zone' },
    ]
    const { baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...props([], alerts)} />
      </Wrapper>,
    )

    const row = baseElement.querySelector<HTMLElement>('[data-entity-id="alert:al_123"]')
    expect(row).toBeTruthy()
    expect(row!.getAttribute('data-entity-label')).toBe('PLTR')
    expect(row!.getAttribute('draggable')).toBe('true')
  })

  it('leaves an id-less alert unwired rather than emitting an unresolvable ref', () => {
    const alerts: PriceAlert[] = [{ ticker: 'PLTR', op: '>=', value: 20, note: 'buy zone' }]
    const { baseElement } = render(
      <Wrapper>
        <MonitorsWorkspace {...props([], alerts)} />
      </Wrapper>,
    )

    // The alert still renders...
    expect(baseElement.querySelector('.intel-mm-alert')).toBeTruthy()
    // ...but claims no entity identity.
    expect(baseElement.querySelector('[data-entity-id^="alert:"]')).toBeNull()
  })
})
