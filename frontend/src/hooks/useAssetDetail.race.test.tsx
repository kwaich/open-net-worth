// @vitest-environment jsdom
//
// Regression for the stale-async race: opening ONWAI triggers a slow refresh; the user
// navigates to another ticker before it finishes; when ONWAI's refresh lands it must NOT
// clobber the now-current asset's view (the "it jumps back to ONWAI" bug).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup } from '@testing-library/react'
import { useAssetDetail, _clearDetailCache } from './useAssetDetail'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

function makeDetail(last: number, over: Record<string, unknown> = {}) {
  return {
    has_ticker: true,
    asset: { ticker: 'X', name: 'X', asset_class: 'equity' },
    price: { last, currency: 'USD', as_of: new Date().toISOString() },
    news: [{ headline: 'n' }],
    key_issues: [],
    last_refreshed_at: new Date().toISOString(),
    ...over,
  }
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  _clearDetailCache()
})
afterEach(() => cleanup())

describe('useAssetDetail — stale-async guard across asset navigation', () => {
  it("a previous asset's slow GET, landing after navigation, does not clobber the new view", async () => {
    let resolveOnwai!: (r: Response) => void
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/assets/onwai')) return new Promise<Response>((r) => (resolveOnwai = r))
      if (url.includes('/assets/ticker2')) return Promise.resolve(okJson(makeDetail(99)))
      return Promise.resolve(okJson(makeDetail(0)))
    })

    const { result, rerender } = renderHook(({ id }) => useAssetDetail(id), {
      initialProps: { id: 'onwai' },
    })
    expect(result.current.data).toBeNull() // ONWAI's GET hasn't landed

    rerender({ id: 'ticker2' }) // user opens another ticker
    await act(async () => {})
    expect(result.current.data?.price.last).toBe(99) // TICKER2 shown

    await act(async () => {
      resolveOnwai(okJson(makeDetail(11))) // ONWAI's GET finally resolves
      await Promise.resolve()
    })
    expect(result.current.data?.price.last).toBe(99) // STILL TICKER2 — not ONWAI(11)
  })

  it("a previous asset's slow REFRESH completing does not switch the view back to it", async () => {
    let resolveRefreshPost!: (r: Response) => void
    let resolveRefetchGet!: (r: Response) => void
    let onwaiGets = 0
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.includes('/assets/onwai/refresh')) {
        return new Promise<Response>((r) => (resolveRefreshPost = r))
      }
      if (url.includes('/assets/onwai')) {
        onwaiGets += 1
        // 1st GET: empty brief → triggers the auto-refresh POST. 2nd GET: the post-refresh
        // refetch (slow/controlled) that, pre-fix, clobbered the new view.
        if (onwaiGets === 1)
          return Promise.resolve(okJson(makeDetail(11, { news: [], key_issues: [] })))
        return new Promise<Response>((r) => (resolveRefetchGet = r))
      }
      if (url.includes('/assets/ticker2')) return Promise.resolve(okJson(makeDetail(99)))
      return Promise.resolve(okJson(makeDetail(0)))
    })

    const { result, rerender } = renderHook(({ id }) => useAssetDetail(id), {
      initialProps: { id: 'onwai' },
    })
    await act(async () => {}) // ONWAI GET#1 lands → auto-refresh POST now pending
    expect(result.current.data?.price.last).toBe(11)

    rerender({ id: 'ticker2' }) // navigate away mid-refresh
    await act(async () => {})
    expect(result.current.data?.price.last).toBe(99)

    await act(async () => {
      resolveRefreshPost(okJson({ ok: true }))
      await Promise.resolve()
      resolveRefetchGet(okJson(makeDetail(11))) // ONWAI's refetch lands AFTER navigation
      await Promise.resolve()
    })
    expect(result.current.data?.price.last).toBe(99) // no jump back to ONWAI
  })
})
