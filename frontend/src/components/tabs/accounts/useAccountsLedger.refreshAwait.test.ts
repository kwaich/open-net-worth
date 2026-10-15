// @vitest-environment jsdom
//
// Regression: the Add-category flow (AccountsTab.createCategory) creates the category via
// POST /api/categories, then called `L.refresh()` fire-and-forget before notifying the caller
// (toast + the picker's onDone/select callback). Since `refresh()` never returned a Promise, the
// caller's onDone fired — selecting the brand-new category id in the picker — BEFORE the
// `categories` state had actually been refetched to include it, so the picker rendered it as
// missing/blank until some unrelated later reload happened to land.
//
// Fix: `refresh()` now returns a Promise that only resolves once `categories` (and the other
// core data) has actually been reloaded, so a caller can `await L.refresh()` before acting on
// the assumption that the new category is present.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
import { useAccountsLedger } from './useAccountsLedger'
import type { Category } from './types'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown): Response {
  return { ok: true, json: async () => body } as Response
}

const NEW_CAT: Category = {
  id: 'cat_new123',
  label: 'Pet care',
  color: '#e0a032',
  kind: 'expense',
  group: 'Custom',
}

function mockFixedEndpoints(url: string): Promise<Response> | null {
  if (url.startsWith('/api/accounts')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/payees')) return Promise.resolve(okJson({ payees: [] }))
  if (url.startsWith('/api/portfolio')) return Promise.resolve(okJson([]))
  if (url.startsWith('/api/prices')) return Promise.resolve(okJson({}))
  if (url.startsWith('/api/transactions'))
    return Promise.resolve(okJson({ transactions: [], total: 0, has_more: false }))
  return null
}

beforeEach(() => {
  mockAuthFetch.mockReset()
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useAccountsLedger.refresh — resolves only after categories actually reload', () => {
  it('the returned Promise settles after categories reflects the newly-created category, not before', async () => {
    let catCallCount = 0
    let resolveSecondCatFetch!: (r: Response) => void

    mockAuthFetch.mockImplementation((url: string) => {
      const fixed = mockFixedEndpoints(url)
      if (fixed) return fixed
      if (url.startsWith('/api/categories')) {
        catCallCount += 1
        if (catCallCount === 1) return Promise.resolve(okJson([])) // initial mount load
        // The refresh triggered after the category was created — held open so the test controls
        // exactly when it lands, mirroring a real network round-trip.
        return new Promise<Response>((r) => (resolveSecondCatFetch = r))
      }
      throw new Error(`unexpected fetch: ${url}`)
    })

    const { result } = renderHook(() => useAccountsLedger())

    await waitFor(() => expect(result.current.hasLoaded).toBe(true))
    expect(result.current.categories).toEqual([])

    let refreshSettled = false
    let refreshPromise!: Promise<void>
    act(() => {
      refreshPromise = result.current.refresh()
      refreshPromise.then(() => {
        refreshSettled = true
      })
    })

    // The refetch is in flight but not resolved yet — refresh() must NOT have settled, and the
    // stale (empty) categories list must still be what's in state.
    await waitFor(() => expect(catCallCount).toBe(2))
    await Promise.resolve()
    expect(refreshSettled).toBe(false)
    expect(result.current.categories).toEqual([])

    // Now let the categories GET land with the newly-created category included.
    await act(async () => {
      resolveSecondCatFetch(okJson([NEW_CAT]))
      await refreshPromise
    })

    expect(refreshSettled).toBe(true)
    expect(result.current.categories).toEqual([NEW_CAT])
  })
})
