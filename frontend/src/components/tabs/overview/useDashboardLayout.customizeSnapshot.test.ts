// @vitest-environment jsdom

import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fetchDashboardLayout, saveDashboardLayout } from '../../../api/dashboard'
import { clearQueryCache } from '../../../state/queryCache'
import type { DashboardState } from './types'
import { useDashboardLayout } from './useDashboardLayout'

vi.mock('../../../api/dashboard', () => ({
  fetchDashboardLayout: vi.fn(),
  saveDashboardLayout: vi.fn(),
}))

const mockFetch = fetchDashboardLayout as ReturnType<typeof vi.fn>
const mockSave = saveDashboardLayout as ReturnType<typeof vi.fn>

const state: DashboardState = {
  activeViewId: 'overview',
  views: [
    { viewId: 'overview', name: 'Overview', widgets: [] },
    { viewId: 'investing', name: 'Investing', widgets: [] },
  ],
}

beforeEach(() => {
  clearQueryCache()
  mockFetch.mockReset().mockResolvedValue(state)
  mockSave.mockReset().mockResolvedValue(undefined)
})

afterEach(() => vi.restoreAllMocks())

describe('useDashboardLayout Customize snapshot', () => {
  it('does not count switching dashboard views as an unsaved layout change', async () => {
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.snapshot())
    act(() => result.current.setActiveViewId('investing'))

    expect(result.current.isDirty).toBe(false)
  })

  it('restores edits across views without moving the user off the current view', async () => {
    const { result } = renderHook(() => useDashboardLayout())
    await waitFor(() => expect(result.current.loading).toBe(false))

    act(() => result.current.snapshot())
    act(() => result.current.renameView('overview', 'Changed'))
    act(() => result.current.setActiveViewId('investing'))
    act(() => result.current.restoreSnapshot())

    expect(result.current.views[0]?.name).toBe('Overview')
    expect(result.current.activeViewId).toBe('investing')
    expect(result.current.isDirty).toBe(false)
  })
})
