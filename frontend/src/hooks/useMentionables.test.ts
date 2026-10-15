// @vitest-environment jsdom
//
// Tests for useMentionables: debounce, AbortController cancel, local+backend merge/dedupe,
// and DOM harvest.
//
// The hook now calls adapters.getDataSource() instead of authFetch directly.
// Tests mock the DataSourceAdapter via vi.mock('../lucius/adapters').
//
// The "recently used mentions" localStorage cache (pushRecentMention et al.) was
// removed 2026-08-11 — see useMentionables.ts's top-of-file comment for why.

import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import type { DataSourceAdapter } from '../lucius/adapters'

// Mock the adapters module before importing the hook.
const mockSearch = vi.fn()
// resolve() is part of the DataSourceAdapter contract (used elsewhere, e.g.
// MentionDetail's resolve-on-tap) — kept here only to satisfy that shape; this
// hook itself never calls it.
const mockResolve = vi.fn()
const mockAdapter: DataSourceAdapter = {
  search: mockSearch,
  resolve: mockResolve,
}

vi.mock('../lucius/adapters', () => ({
  getDataSource: () => mockAdapter,
  wireDataSource: vi.fn(),
}))

import { useMentionables } from './useMentionables'
import { detectAtToken } from '../components/chat/ChatPanel'

// ── Helpers ──────────────────────────────────────────────────────────────────

const BACKEND_ROWS = [
  {
    type: 'account',
    id: 'acc-1',
    label: 'Vantage Brokerage USD',
    sublabel: '$12,340',
    value: 'acc-1',
    icon: '⬡',
  },
  {
    type: 'holding',
    id: 'pos-1',
    label: 'AAPL',
    sublabel: '50 shares',
    value: 'pos-1',
    icon: '◈',
  },
]

// ── Setup ────────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  // shouldAdvanceTime: true lets the real system clock advance alongside fake timers,
  // which keeps waitFor's internal polling (setTimeout-based) functional.
  vi.useFakeTimers({ shouldAdvanceTime: true })
  localStorage.clear()
  // Clear any lingering data-entity-id nodes.
  document.body.innerHTML = ''
  // Default: search returns empty; resolve unused by hook.
  mockSearch.mockResolvedValue({ items: [], nextCursor: null })
  mockResolve.mockResolvedValue([])
})

afterEach(() => {
  vi.useRealTimers()
})

// ── Tests ────────────────────────────────────────────────────────────────────

describe('useMentionables', () => {
  describe('backend fetch', () => {
    it('debounces the backend fetch by 150 ms', async () => {
      mockSearch.mockResolvedValue({ items: BACKEND_ROWS, nextCursor: null })

      renderHook(() => useMentionables('aapl'))

      // Immediately after render — fetch should NOT have fired yet.
      expect(mockSearch).not.toHaveBeenCalled()

      // Advance past the debounce window.
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(mockSearch).toHaveBeenCalledOnce())
    })

    it('cancels the in-flight request when query changes before debounce fires', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const { rerender } = renderHook(({ query }) => useMentionables(query), {
        initialProps: { query: 'aa' },
      })

      // Change query before 150 ms.
      await act(async () => {
        vi.advanceTimersByTime(80)
      })
      rerender({ query: 'aapl' })

      // Advance through the debounce of the second query.
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(mockSearch).toHaveBeenCalledOnce())

      // The search call should be for the second query.
      const callArg = mockSearch.mock.calls[0]![0]
      expect(callArg.q).toBe('aapl')
    })

    it('aborts the prior request via AbortController when query changes mid-flight', async () => {
      let capturedSignal: AbortSignal | undefined
      mockSearch.mockImplementation((q: { signal?: AbortSignal }) => {
        capturedSignal = q.signal
        // Return a never-resolving promise to simulate a slow network.
        return new Promise(() => {})
      })

      const { rerender, result } = renderHook(({ query }) => useMentionables(query), {
        initialProps: { query: 'a' },
      })

      // Let the first fetch start.
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      expect(capturedSignal).toBeDefined()
      expect(capturedSignal!.aborted).toBe(false)

      // Change query — should abort the first controller.
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })
      rerender({ query: 'b' })
      await act(async () => {
        vi.advanceTimersByTime(160)
      })

      expect(capturedSignal!.aborted).toBe(true)
      await waitFor(() => expect(result.current.loading).toBe(false))
    })

    it('merges backend rows after local rows (deduped by type+value)', async () => {
      // Put a row with the same type+value as a backend row into DOM so we can
      // verify the local row stays pinned and gets its sublabel enriched.
      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-1'
      el.dataset.entityLabel = 'Vantage Brokerage USD'
      document.body.appendChild(el)

      mockSearch.mockResolvedValue({ items: BACKEND_ROWS, nextCursor: null })

      const { result } = renderHook(() => useMentionables(''))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      const items = result.current.items
      // Local DOM row for acc-1 should be first; its sublabel should be enriched.
      const accRow = items.find((r) => r.type === 'account' && r.value === 'acc-1')
      expect(accRow).toBeDefined()
      expect(accRow!.sublabel).toBe('$12,340')

      // Backend enrichment must NOT strip the onScreen flag from the local row.
      expect(accRow!.onScreen).toBe(true)

      // Backend-only row (pos-1) should be appended and NOT flagged as onScreen.
      const holdingRow = items.find((r) => r.type === 'holding' && r.value === 'pos-1')
      expect(holdingRow).toBeDefined()
      expect(holdingRow!.onScreen).toBeFalsy()

      // No duplicates.
      const dedupedCount = new Set(items.map((r) => `${r.type}:${r.value}`)).size
      expect(dedupedCount).toBe(items.length)
    })

    it('sets loading=false and returns empty items on rejected search', async () => {
      mockSearch.mockRejectedValue(new Error('non-ok response'))

      const { result } = renderHook(() => useMentionables('aapl'))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      // No backend rows — only whatever local rows exist.
      expect(result.current.items.every((r) => r.sublabel !== '$12,340')).toBe(true)
    })

    it('sets loading=false and returns empty items on network error', async () => {
      mockSearch.mockRejectedValue(new Error('network'))

      const { result } = renderHook(() => useMentionables('x'))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.items).toHaveLength(0)
    })
  })

  describe('error state', () => {
    it('sets error=true on rejected search', async () => {
      mockSearch.mockRejectedValue(new Error('fetch error'))

      const { result } = renderHook(() => useMentionables('aapl'))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.error).toBe(true)
    })

    it('sets error=true on network failure', async () => {
      mockSearch.mockRejectedValue(new Error('network'))

      const { result } = renderHook(() => useMentionables('foo'))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.error).toBe(true)
    })

    it('sets error=false on a successful response', async () => {
      mockSearch.mockResolvedValue({ items: BACKEND_ROWS, nextCursor: null })

      const { result } = renderHook(() => useMentionables('aapl'))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.error).toBe(false)
    })

    it('retry() re-runs the fetch and clears the error on success', async () => {
      // First call fails.
      mockSearch.mockRejectedValueOnce(new Error('fail'))

      const { result } = renderHook(() => useMentionables('aapl'))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.error).toBe(true))

      // Second call succeeds (retry).
      mockSearch.mockResolvedValueOnce({ items: BACKEND_ROWS, nextCursor: null })
      await act(async () => {
        result.current.retry()
      })
      await waitFor(() => expect(result.current.error).toBe(false))
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.items.some((r) => r.label === 'AAPL')).toBe(true)
    })
  })

  describe('remoteItems', () => {
    it('remoteItems contains backend-only rows not in the local DOM harvest', async () => {
      // acc-1 is both in DOM and backend; pos-1 is backend-only.
      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-1'
      el.dataset.entityLabel = 'Vantage Brokerage USD'
      document.body.appendChild(el)

      mockSearch.mockResolvedValue({ items: BACKEND_ROWS, nextCursor: null })

      const { result } = renderHook(() => useMentionables(''))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      // pos-1 (holding) is not in local DOM → should be in remoteItems.
      expect(result.current.remoteItems.some((r) => r.value === 'pos-1')).toBe(true)
      // acc-1 is in local DOM → should NOT be in remoteItems.
      expect(result.current.remoteItems.some((r) => r.value === 'acc-1')).toBe(false)
    })

    it('remoteItems is empty when all backend rows were already local', async () => {
      // Put both backend rows into the DOM.
      for (const row of BACKEND_ROWS) {
        const el = document.createElement('div')
        el.dataset.entityId = `${row.type}:${row.value}`
        el.dataset.entityLabel = row.label
        document.body.appendChild(el)
      }

      mockSearch.mockResolvedValue({ items: BACKEND_ROWS, nextCursor: null })

      const { result } = renderHook(() => useMentionables(''))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.remoteItems).toHaveLength(0)
    })

    it('remoteItems is empty when backend returns nothing', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const { result } = renderHook(() => useMentionables(''))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      expect(result.current.remoteItems).toHaveLength(0)
    })
  })

  describe('DOM harvest', () => {
    it('returns on-screen entities from data-entity-id attributes', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el1 = document.createElement('span')
      el1.dataset.entityId = 'account:acc-42'
      el1.dataset.entityLabel = 'Checking'
      document.body.appendChild(el1)

      const el2 = document.createElement('span')
      el2.dataset.entityId = 'holding:pos-7'
      el2.dataset.entityLabel = 'TSLA'
      document.body.appendChild(el2)

      const { result } = renderHook(() => useMentionables(''))
      // Local rows are synchronous — present before fetch resolves.
      expect(result.current.items.some((r) => r.type === 'account' && r.value === 'acc-42')).toBe(
        true,
      )
      expect(result.current.items.some((r) => r.type === 'holding' && r.value === 'pos-7')).toBe(
        true,
      )
    })

    it('DOM-harvested rows have onScreen=true', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('span')
      el.dataset.entityId = 'account:acc-77'
      el.dataset.entityLabel = 'Brokerage'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables(''))
      const row = result.current.items.find((r) => r.value === 'acc-77')
      expect(row).toBeDefined()
      expect(row!.onScreen).toBe(true)
    })

    it('harvests data-entity-sublabel and surfaces it immediately as row.sublabel', async () => {
      // The backend never fires in this test — the DOM-supplied sublabel must be immediately
      // available without waiting for the 150ms backend round-trip.
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'transaction:txn-abc'
      el.dataset.entityLabel = 'PLTR BUY 10 @ 115.5'
      el.dataset.entitySublabel = 'Vantage Brokerage • 2026-05-10 • −$1,155.00'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables(''))
      // Immediately — no timer advance needed.
      const row = result.current.items.find(
        (r) => r.type === 'transaction' && r.value === 'txn-abc',
      )
      expect(row).toBeDefined()
      expect(row!.sublabel).toBe('Vantage Brokerage • 2026-05-10 • −$1,155.00')
      expect(row!.onScreen).toBe(true)
    })

    it('backend sublabel overwrites DOM sublabel and preserves onScreen flag', async () => {
      // An on-screen transaction emits a DOM sublabel. The backend returns a richer version
      // (base-currency, authoritative payee name). The backend wins, but onScreen stays true.
      const el = document.createElement('div')
      el.dataset.entityId = 'transaction:txn-rich'
      el.dataset.entityLabel = 'Anthropic'
      el.dataset.entitySublabel = 'Salary • Lighthouse Bank • 2026-05-01 • +$8,000 SGD'
      document.body.appendChild(el)

      const backendRow = {
        type: 'transaction',
        id: 'txn-rich',
        label: 'Anthropic',
        sublabel: 'Anthropic • Lighthouse Bank • 2026-05-01 • +$5,800 USD',
        value: 'txn-rich',
        icon: '⬡',
      }
      mockSearch.mockResolvedValue({ items: [backendRow], nextCursor: null })

      const { result } = renderHook(() => useMentionables(''))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      const row = result.current.items.find(
        (r) => r.type === 'transaction' && r.value === 'txn-rich',
      )
      expect(row).toBeDefined()
      // Backend sublabel wins.
      expect(row!.sublabel).toBe('Anthropic • Lighthouse Bank • 2026-05-01 • +$5,800 USD')
      // onScreen flag preserved from the DOM harvest.
      expect(row!.onScreen).toBe(true)
    })

    it('on-screen transaction without DOM sublabel gets enriched by backend when returned', async () => {
      // This is the original dedupeRows enrichment path — still works even without
      // data-entity-sublabel on the DOM element.
      const el = document.createElement('div')
      el.dataset.entityId = 'transaction:txn-nodom'
      el.dataset.entityLabel = 'Shopee'
      document.body.appendChild(el)

      const backendRow = {
        type: 'transaction',
        id: 'txn-nodom',
        label: 'Shopee',
        sublabel: 'Shopee • Meridian Bank • 2026-05-15 • −$34.50 SGD',
        value: 'txn-nodom',
        icon: '⬡',
      }
      mockSearch.mockResolvedValue({ items: [backendRow], nextCursor: null })

      const { result } = renderHook(() => useMentionables(''))
      await act(async () => {
        vi.advanceTimersByTime(160)
      })
      await waitFor(() => expect(result.current.loading).toBe(false))

      const row = result.current.items.find(
        (r) => r.type === 'transaction' && r.value === 'txn-nodom',
      )
      expect(row).toBeDefined()
      expect(row!.sublabel).toBe('Shopee • Meridian Bank • 2026-05-15 • −$34.50 SGD')
      expect(row!.onScreen).toBe(true)
    })

    it('ignores data-entity-id elements with missing colon separator', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'malformed'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables(''))
      expect(result.current.items.find((r) => r.value === 'malformed')).toBeUndefined()
    })

    it('dedupes multiple DOM nodes with the same type:value', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      for (let i = 0; i < 3; i++) {
        const el = document.createElement('div')
        el.dataset.entityId = 'account:dup'
        el.dataset.entityLabel = 'Same account'
        document.body.appendChild(el)
      }

      const { result } = renderHook(() => useMentionables(''))
      const dupCount = result.current.items.filter(
        (r) => r.type === 'account' && r.value === 'dup',
      ).length
      expect(dupCount).toBe(1)
    })

    it('filters DOM rows by query (case-insensitive substring)', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el1 = document.createElement('div')
      el1.dataset.entityId = 'account:a1'
      el1.dataset.entityLabel = 'Savings'
      document.body.appendChild(el1)

      const el2 = document.createElement('div')
      el2.dataset.entityId = 'account:a2'
      el2.dataset.entityLabel = 'Checking'
      document.body.appendChild(el2)

      const { result } = renderHook(() => useMentionables('sav'))
      expect(result.current.items.some((r) => r.label === 'Savings')).toBe(true)
      expect(result.current.items.some((r) => r.label === 'Checking')).toBe(false)
    })

    it('harvests data-entity-search and exposes it as row.search', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'holding:pos-search'
      el.dataset.entityLabel = 'ONWAI'
      el.dataset.entitySearch = 'ONWAI XETRA Vantage Brokerage'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables(''))
      const row = result.current.items.find((r) => r.value === 'pos-search')
      expect(row).toBeDefined()
      expect(row!.search).toBe('ONWAI XETRA Vantage Brokerage')
    })

    it('matches a row by data-entity-search even when the label does NOT contain the query', async () => {
      // The key new test: chip label is "ONWAI" but the user types "vantage" or "xetra".
      // The row must match via its broad search string, not the label.
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'holding:pos-broad'
      el.dataset.entityLabel = 'ONWAI'
      el.dataset.entitySearch = 'ONWAI XETRA Vantage Brokerage'
      document.body.appendChild(el)

      // Query "xetra" does NOT appear in the label "ONWAI" — must hit search.
      const { result } = renderHook(() => useMentionables('xetra'))
      expect(result.current.items.some((r) => r.value === 'pos-broad')).toBe(true)
    })

    it('a row with no data-entity-search still matches by label (no regression)', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-nms'
      el.dataset.entityLabel = 'Brokerage'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables('brokerage'))
      expect(result.current.items.some((r) => r.value === 'acc-nms')).toBe(true)
    })

    it('a row with data-entity-search does NOT leak into a non-matching query', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'holding:pos-nomatch'
      el.dataset.entityLabel = 'AAPL'
      el.dataset.entitySearch = 'AAPL Apple Inc Vantage Brokerage'
      document.body.appendChild(el)

      // "samsung" is in neither label nor search — must not match.
      const { result } = renderHook(() => useMentionables('samsung'))
      expect(result.current.items.some((r) => r.value === 'pos-nomatch')).toBe(false)
    })

    it('harvests data-entity-color onto row.color for account DOM elements', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-color'
      el.dataset.entityLabel = 'Meridian Multiplier'
      el.dataset.entityColor = '#ff5733'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables(''))
      const row = result.current.items.find((r) => r.type === 'account' && r.value === 'acc-color')
      expect(row).toBeDefined()
      expect(row!.color).toBe('#ff5733')
    })

    it('row.color is undefined when data-entity-color is absent', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-nocolor'
      el.dataset.entityLabel = 'Compass'
      document.body.appendChild(el)

      const { result } = renderHook(() => useMentionables(''))
      const row = result.current.items.find(
        (r) => r.type === 'account' && r.value === 'acc-nocolor',
      )
      expect(row).toBeDefined()
      expect(row!.color).toBeUndefined()
    })

    it('DOM harvest skips elements inside data-ctx-ignore subtrees (F9 fix)', async () => {
      // An entity inside a data-ctx-ignore region must NOT appear in the harvest.
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const wrapper = document.createElement('div')
      wrapper.setAttribute('data-ctx-ignore', '')

      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-ignored'
      el.dataset.entityLabel = 'Secret Account'
      wrapper.appendChild(el)
      document.body.appendChild(wrapper)

      const { result } = renderHook(() => useMentionables(''))
      expect(result.current.items.find((r) => r.value === 'acc-ignored')).toBeUndefined()
    })

    it('DOM harvest skips elements inside data-ctx-secret subtrees (F9 fix)', async () => {
      // An entity inside a data-ctx-secret region must NOT appear in the harvest.
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const wrapper = document.createElement('div')
      wrapper.setAttribute('data-ctx-secret', '')

      const el = document.createElement('div')
      el.dataset.entityId = 'account:acc-secret'
      el.dataset.entityLabel = 'API Key Account'
      wrapper.appendChild(el)
      document.body.appendChild(wrapper)

      const { result } = renderHook(() => useMentionables(''))
      expect(result.current.items.find((r) => r.value === 'acc-secret')).toBeUndefined()
    })
  })

  describe('re-harvests on lx-rescan (tab-switch staleness, owner report 2026-08-11)', () => {
    it('does not pick up a new on-screen entity on a plain re-render, only after lx-rescan', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })

      const elA = document.createElement('span')
      elA.dataset.entityId = 'account:acc-tabA'
      elA.dataset.entityLabel = 'Tab A Account'
      document.body.appendChild(elA)

      const { result, rerender } = renderHook(({ q }) => useMentionables(q), {
        initialProps: { q: '' },
      })
      expect(result.current.items.some((r) => r.value === 'acc-tabA')).toBe(true)

      // Simulate switching tabs: the old tab's DOM is gone, a different tab's is in its
      // place — mirrors ScreenContextBar's own comment ("otherwise it stays on the old
      // screen's context until the user scrolls").
      elA.remove()
      const elB = document.createElement('span')
      elB.dataset.entityId = 'account:acc-tabB'
      elB.dataset.entityLabel = 'Tab B Account'
      document.body.appendChild(elB)

      // A re-render alone (e.g. the user typing) must not silently pick this up.
      rerender({ q: 'a' })
      expect(result.current.items.some((r) => r.value === 'acc-tabB')).toBe(false)

      // The event RouterShell actually dispatches on every tab/route change.
      act(() => {
        window.dispatchEvent(new Event('lx-rescan'))
      })
      rerender({ q: 'a' })
      expect(result.current.items.some((r) => r.value === 'acc-tabB')).toBe(true)
      expect(result.current.items.some((r) => r.value === 'acc-tabA')).toBe(false)
    })
  })

  describe('no recents cache (removal regression, owner decision 2026-08-11)', () => {
    it('never calls resolve() — the hook has nothing left to validate against the server', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })
      renderHook(() => useMentionables(''))
      await act(async () => {
        await Promise.resolve()
      })
      expect(mockResolve).not.toHaveBeenCalled()
    })

    it('a DOM-harvested row disappearing from the screen is not resurrected by any local cache', async () => {
      mockSearch.mockResolvedValue({ items: [], nextCursor: null })
      const el = document.createElement('span')
      el.dataset.entityId = 'account:acc-gone'
      el.dataset.entityLabel = 'Temp Account'
      document.body.appendChild(el)

      const { result, rerender } = renderHook(({ q }) => useMentionables(q), {
        initialProps: { q: '' },
      })
      expect(result.current.items.some((r) => r.value === 'acc-gone')).toBe(true)

      el.remove()
      act(() => {
        window.dispatchEvent(new Event('lx-rescan'))
      })
      rerender({ q: '' })
      expect(result.current.items.some((r) => r.value === 'acc-gone')).toBe(false)
    })
  })
})

// ── detectAtToken word-boundary tests ────────────────────────────────────────

describe('detectAtToken', () => {
  it('does NOT trigger on a mid-word @ (email address "foo@bar")', () => {
    const value = 'foo@bar'
    expect(detectAtToken(value, value.length)).toBeNull()
  })

  it('triggers at start-of-string: "@app" with caret at end', () => {
    const value = '@app'
    const result = detectAtToken(value, value.length)
    expect(result).not.toBeNull()
    expect(result!.atPos).toBe(0)
    expect(result!.query).toBe('app')
  })

  it('triggers after a leading space: " @app" with caret at end', () => {
    const value = ' @app'
    const result = detectAtToken(value, value.length)
    expect(result).not.toBeNull()
    expect(result!.atPos).toBe(1)
    expect(result!.query).toBe('app')
  })

  it('triggers mid-sentence: "hi @app" with caret at end', () => {
    const value = 'hi @app'
    const result = detectAtToken(value, value.length)
    expect(result).not.toBeNull()
    expect(result!.atPos).toBe(3)
    expect(result!.query).toBe('app')
  })

  it('does NOT trigger when caret is before the @', () => {
    const value = 'foo@bar'
    // caret is before the @
    expect(detectAtToken(value, 2)).toBeNull()
  })

  it('returns null when there is no @ before the caret in the current token', () => {
    expect(detectAtToken('hello world', 11)).toBeNull()
  })
})
