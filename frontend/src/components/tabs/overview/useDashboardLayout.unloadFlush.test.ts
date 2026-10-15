// @vitest-environment jsdom
//
// Regression: switching the active dashboard view (e.g. Overview -> Reports
// -> back to Overview) only persists via a 700ms-debounced save. If the page
// unloads (hard refresh) before that timer fires, the `beforeunload` flush
// must actually reach the server — otherwise the switch is silently lost and
// the next load re-opens whichever view was last durably saved, not the one
// the user was looking at. Pins the fix: the flush now passes
// `keepalive: true` so the browser doesn't abort the in-flight PUT while
// tearing down the document.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useDashboardLayout } from './useDashboardLayout'
import { clearQueryCache } from '../../../state/queryCache'
import type { DashboardState } from './types'

vi.mock('../../../api/dashboard', () => ({
  fetchDashboardLayout: vi.fn(),
  saveDashboardLayout: vi.fn(),
}))
import { fetchDashboardLayout, saveDashboardLayout } from '../../../api/dashboard'
const mockFetch = fetchDashboardLayout as ReturnType<typeof vi.fn>
const mockSave = saveDashboardLayout as ReturnType<typeof vi.fn>

function twoViewState(): DashboardState {
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
  cleanup()
  vi.restoreAllMocks()
})

describe('useDashboardLayout — beforeunload flush', () => {
  it('flushes a pending view-switch save with keepalive on page unload', async () => {
    mockFetch.mockResolvedValue(twoViewState())
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.setActiveViewId('v2'))
    // The debounced save hasn't fired yet (no timers advanced) — this is the
    // exact window a hard refresh lands in.
    expect(mockSave).not.toHaveBeenCalled()

    act(() => {
      window.dispatchEvent(new Event('beforeunload'))
    })

    expect(mockSave).toHaveBeenCalledTimes(1)
    expect(mockSave).toHaveBeenCalledWith(expect.objectContaining({ activeViewId: 'v2' }), {
      keepalive: true,
    })
  })

  it('does not re-send the layout on unload once the debounced save has landed', async () => {
    // The flush used to test only that a timer had EVER been set: after any save,
    // every reload re-sent this page's copy and overwrote whatever had changed on
    // the server since — another device's edit, a view Lucius built.
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      mockFetch.mockResolvedValue(twoViewState())
      const { result } = renderHook(() => useDashboardLayout())
      await waitFor(() => expect(result.current.loading).toBe(false))

      act(() => result.current.setActiveViewId('v2'))
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000)
      })
      expect(mockSave).toHaveBeenCalledTimes(1)

      act(() => {
        window.dispatchEvent(new Event('beforeunload'))
      })
      expect(mockSave).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })
})
