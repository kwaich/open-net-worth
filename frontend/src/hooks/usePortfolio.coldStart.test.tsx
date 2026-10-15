// @vitest-environment jsdom
//
// Regression: the installed PWA "refreshed and remounted" on every open (2026-07-31).
//
// An iOS standalone PWA is torn down when backgrounded, so every reopen is a cold start with an
// empty in-memory query cache. `usePortfolio` had one stage — `GET /api/portfolio`, which
// refreshes prices through Yahoo/CoinGecko (10-15s provider timeouts) BEFORE responding — and
// the shell withheld the entire tab tree until it resolved. Multi-second blank, then everything
// mounted at once.
//
// These tests pin the three stages that replaced it. They are about WHEN content is available,
// not about any number on it: no money is asserted here.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, cleanup, waitFor, act } from '@testing-library/react'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../api/client'
import { usePortfolio } from './usePortfolio'
import { clearQueryCache, setCachedQueryData } from '../state/queryCache'

const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function response(rows: Array<Record<string, unknown>>, isLive: boolean) {
  return {
    base_currency: 'USD',
    rows,
    portfolio_total: rows.length,
    unvested_base: 0,
    holdings_by_account: {},
    is_live: isLive,
    fx: { USD: 1 },
  }
}

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

/** A promise plus its resolver — lets a test hold the live read open and assert what the app
 *  can already show while the provider round trip is still outstanding. */
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  localStorage.clear()
  clearQueryCache()
})

afterEach(() => {
  cleanup()
})

describe('usePortfolio — cold start paints before live prices land', () => {
  it('reads last-known prices first and renders them while the live read is still in flight', async () => {
    const live = deferred<Response>()
    mockAuthFetch.mockImplementation((url: string) =>
      url === '/api/portfolio?live=0'
        ? Promise.resolve(okJson(response([{ ticker: 'AAA' }], false)))
        : live.promise,
    )

    const { result } = renderHook(() => usePortfolio())

    // The stage that decides whether anything renders: content is available, and marked NOT
    // live, without the provider round trip having answered.
    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    expect(result.current.rows).toHaveLength(1)
    expect(result.current.isLive).toBe(false)

    await act(async () => {
      live.resolve(okJson(response([{ ticker: 'AAA' }, { ticker: 'BBB' }], true)))
      await live.promise
    })

    // …and the live read then replaces it in place — no unmount, no second loading state.
    await waitFor(() => expect(result.current.isLive).toBe(true))
    expect(result.current.rows).toHaveLength(2)
  })

  it('renders the seeded cache entry before ANY request resolves — the reopen case', async () => {
    // What a reopened PWA has: `state/queryCache` hydrated this entry from localStorage before
    // React rendered. Nothing ever answers here — the "reopened on a bad connection" case,
    // where the old shell showed "Loading portfolio…" indefinitely.
    setCachedQueryData('/api/portfolio', response([{ ticker: 'AAA' }], true))
    mockAuthFetch.mockImplementation(() => new Promise<Response>(() => {}))

    const { result } = renderHook(() => usePortfolio())

    // Synchronously on the first render — not after an effect, not after a fetch.
    expect(result.current.hasLoaded).toBe(true)
    expect(result.current.rows).toHaveLength(1)
    // Seeded prices are of unknown age, so they must never claim to be live…
    expect(result.current.isLive).toBe(false)
    // …and the entry's own fetch time is what a caller reports, not "just now".
    expect(result.current.lastUpdated).not.toBeNull()
    expect(result.current.lastUpdated!.getTime()).toBeLessThanOrEqual(Date.now())
  })

  it('still loads when the last-known read fails — it is an optimization, not a dependency', async () => {
    mockAuthFetch.mockImplementation((url: string) =>
      url === '/api/portfolio?live=0'
        ? Promise.resolve({ ok: false, status: 500, json: async () => ({}) } as Response)
        : Promise.resolve(okJson(response([{ ticker: 'AAA' }], true))),
    )

    const { result } = renderHook(() => usePortfolio())

    await waitFor(() => expect(result.current.isLive).toBe(true))
    expect(result.current.rows).toHaveLength(1)
  })
})
