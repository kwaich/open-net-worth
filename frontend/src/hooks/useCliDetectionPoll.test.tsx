// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CLI_DETECTION_INTERVAL, useCliDetectionPoll } from './useCliDetectionPoll'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('CLI installation polling', () => {
  it('checks every five seconds, skips hidden pages, and checks immediately on return', async () => {
    vi.useFakeTimers()
    const refresh = vi.fn().mockResolvedValue(undefined)
    const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
    renderHook(() => useCliDetectionPoll(refresh, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL)
    })
    expect(refresh).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('hidden')
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL * 2)
    })
    expect(refresh).toHaveBeenCalledTimes(1)
    visibility.mockReturnValue('visible')
    await act(async () => {
      document.dispatchEvent(new Event('visibilitychange'))
    })
    expect(refresh).toHaveBeenCalledTimes(2)
  })

  it('coalesces overlapping manual, focus and timer checks', async () => {
    vi.useFakeTimers()
    let release!: () => void
    const refresh = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    const { result } = renderHook(() => useCliDetectionPoll(refresh, true))
    let manual!: Promise<unknown>
    await act(async () => {
      manual = result.current.check()
      window.dispatchEvent(new Event('focus'))
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL * 2)
    })
    expect(refresh).toHaveBeenCalledTimes(1)
    await act(async () => {
      release()
      await manual
    })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL)
    })
    expect(refresh).toHaveBeenCalledTimes(2)
    await act(async () => {
      release()
    })
  })

  it('stops when the stage closes and removes listeners on unmount', async () => {
    vi.useFakeTimers()
    const refresh = vi.fn().mockResolvedValue(undefined)
    const { rerender, unmount } = renderHook(
      ({ enabled }) => useCliDetectionPoll(refresh, enabled),
      { initialProps: { enabled: true } },
    )
    rerender({ enabled: false })
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL)
    })
    expect(refresh).not.toHaveBeenCalled()
    rerender({ enabled: true })
    unmount()
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
      document.dispatchEvent(new Event('visibilitychange'))
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL)
    })
    expect(refresh).not.toHaveBeenCalled()
  })

  it('reports a failed background check and clears the notice after recovery', async () => {
    vi.useFakeTimers()
    const refresh = vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(undefined)
    const { result } = renderHook(() => useCliDetectionPoll(refresh, true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(CLI_DETECTION_INTERVAL)
    })
    expect(result.current.error).toBe('Could not check installations. Check again.')
    await act(async () => {
      window.dispatchEvent(new Event('focus'))
    })
    expect(result.current.error).toBeNull()
  })
})
