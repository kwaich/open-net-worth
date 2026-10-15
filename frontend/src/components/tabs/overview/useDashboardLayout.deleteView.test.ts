// @vitest-environment jsdom
//
// Regression (parity item 12): `deleteView` must refuse to delete the
// default/first view or the last-remaining view — mirrors the design
// handoff's own guard (`vs.views.length <= 1 || vs.views[0].id === id) return
// vs`). Ported alongside the grid engine; this pins the fix so it can't
// silently regress the way the port's initial `deleteView` had (no guard at
// all — any view, including the first, was deletable).

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'
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
      { viewId: 'v2', name: 'Investing', widgets: [] },
    ],
  }
}

beforeEach(() => {
  // The layout query cache is module-level: without this, the previous test's
  // state is served from cache and this test's own mockResolvedValue never lands.
  clearQueryCache()
  mockFetch.mockReset()
  mockSave.mockReset()
  mockSave.mockResolvedValue(undefined)
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useDashboardLayout.deleteView — never deletes the default or last view', () => {
  it('refuses to delete the first (default) view even when other views exist', async () => {
    mockFetch.mockResolvedValue(twoViewState())
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.views.map((v) => v.viewId)).toEqual(['v1', 'v2'])

    act(() => result.current.deleteView('v1'))

    expect(result.current.views.map((v) => v.viewId)).toEqual(['v1', 'v2'])
    expect(result.current.activeViewId).toBe('v1')
  })

  it('refuses to delete the only remaining view', async () => {
    mockFetch.mockResolvedValue({
      activeViewId: 'solo',
      views: [{ viewId: 'solo', name: 'Overview', widgets: [] }],
    })
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.deleteView('solo'))

    expect(result.current.views).toHaveLength(1)
  })

  it('deletes a NON-default view and falls back the active selection', async () => {
    mockFetch.mockResolvedValue(twoViewState())
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.setActiveViewId('v2'))
    act(() => result.current.deleteView('v2'))

    expect(result.current.views.map((v) => v.viewId)).toEqual(['v1'])
    expect(result.current.activeViewId).toBe('v1')
  })
})
