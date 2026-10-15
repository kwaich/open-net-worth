// @vitest-environment jsdom
//
// Regression: a hard refresh re-hydrates the query cache from its localStorage
// mirror before this hook's mount effect ever runs. That mirror only updates
// when a real GET/revalidate lands — and since `/api/dashboard/layout` has no
// `useQueryCache` subscribers anywhere in the app, a save's
// `invalidateQueryCache` call never triggers one (nothing is listening). Left
// unforced, `fetchCachedQuery`'s default 30s staleTime meant a reload could
// serve that stale mirror indefinitely, showing whatever view was last
// actually captured into it rather than the view the user was just on — the
// "switch view -> refresh -> reverts to an old view" report. Pins the fix:
// the mount fetch now passes `force: true`, so it always confirms
// `activeViewId` against the server instead of trusting the cache.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useDashboardLayout } from './useDashboardLayout'
import { clearQueryCache, setCachedQueryData } from '../../../state/queryCache'
import type { DashboardState } from './types'

vi.mock('../../../api/dashboard', () => ({
  fetchDashboardLayout: vi.fn(),
  saveDashboardLayout: vi.fn(),
}))
import { fetchDashboardLayout, saveDashboardLayout } from '../../../api/dashboard'
const mockFetch = fetchDashboardLayout as ReturnType<typeof vi.fn>
const mockSave = saveDashboardLayout as ReturnType<typeof vi.fn>

function serverState(): DashboardState {
  return {
    activeViewId: 'v1',
    views: [
      { viewId: 'v1', name: 'Overview', widgets: [] },
      { viewId: 'v2', name: 'Reports', widgets: [] },
    ],
  }
}

beforeEach(() => {
  clearQueryCache()
  mockFetch.mockReset()
  mockSave.mockReset()
  mockSave.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDashboardLayout — mount always confirms activeViewId against the server', () => {
  it('ignores a fresh (not-yet-stale) cached snapshot and uses the live GET result', async () => {
    // Simulates the localStorage-hydrated mirror on a fresh page load, left over from
    // whenever a real GET last happened to land — here, still pointing at "Reports".
    setCachedQueryData('/api/dashboard/layout', {
      activeViewId: 'v2',
      views: [
        { viewId: 'v1', name: 'Overview', widgets: [] },
        { viewId: 'v2', name: 'Reports', widgets: [] },
      ],
    })
    mockFetch.mockResolvedValue(serverState())

    const { result } = renderHook(() => useDashboardLayout())
    // NOT `waitFor(loading === false)` — the seeded cache above makes `loading` start false
    // immediately (that's the instant-paint behavior working as intended), which would make
    // this assertion race the async forced fetch instead of actually waiting on it.
    await waitFor(() => expect(result.current.activeViewId).toBe('v1'))

    expect(mockFetch).toHaveBeenCalledTimes(1)
  })
})
