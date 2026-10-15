// @vitest-environment jsdom
//
// Which view the Dashboard opens on is remembered for the VISIT, not forever (owner,
// 2026-10-10): a fresh visit lands on the first view, the home, whatever the saved layout's
// activeViewId says; within a visit (a refresh included — sessionStorage survives it) the
// view you picked is kept.

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

const saved = (): DashboardState => ({
  activeViewId: 'v2',
  views: [
    { viewId: 'v1', name: 'Overview', widgets: [] },
    { viewId: 'v2', name: 'Insights', widgets: [] },
  ],
})

beforeEach(() => {
  clearQueryCache()
  sessionStorage.clear()
  mockFetch.mockReset()
  mockSave.mockReset()
  mockSave.mockResolvedValue(undefined)
  mockFetch.mockResolvedValue(saved())
})
afterEach(() => vi.restoreAllMocks())

describe('useDashboardLayout — the open view is remembered for the visit', () => {
  it('opens a fresh visit on the first view, not the last one saved', async () => {
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.activeViewId).toBe('v1')
  })

  it('keeps the view picked earlier in the same visit', async () => {
    const first = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(first.result.current.loading).toBe(false))
    act(() => first.result.current.setActiveViewId('v2'))
    first.unmount()
    clearQueryCache()
    const again = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(again.result.current.loading).toBe(false))
    expect(again.result.current.activeViewId).toBe('v2')
  })
})
