// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => navigate }))

import { useIntelTab } from './useIntelTab'
import type { Watchlist } from './WatchlistsSection'

const LISTS: Watchlist[] = []

afterEach(() => {
  navigate.mockReset()
  localStorage.clear()
})

describe('useIntelTab — the ?sub= mirror', () => {
  it('writes the tab into the URL once the lists load', () => {
    renderHook(() => useIntelTab({ watchlists: LISTS }))
    expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ to: '/intel', replace: true }))
  })

  it('leaving Manage watchlists for Settings does not bounce back to /intel', () => {
    // Regression: the workspace's "Monitoring & alerts" opens /settings?sub=intel. Intel stays
    // mounted behind the Settings overlay, and monitorsOpen flipping to false used to re-run the
    // mirror and navigate straight back to /intel.
    const { rerender } = renderHook(
      (p: { monitorsOpen: boolean; backdrop: boolean }) => useIntelTab({ watchlists: LISTS, ...p }),
      { initialProps: { monitorsOpen: true, backdrop: false } },
    )
    expect(navigate).not.toHaveBeenCalled()
    rerender({ monitorsOpen: false, backdrop: true })
    expect(navigate).not.toHaveBeenCalled()
    // Closing Settings hands the URL back to Intel.
    rerender({ monitorsOpen: false, backdrop: false })
    expect(navigate).toHaveBeenCalledTimes(1)
  })
})
