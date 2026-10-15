// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { PULSE_SORT_KEYS, usePulseSort } from './usePulseSort'

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('usePulseSort', () => {
  it('starts on "Most urgent" and remembers a choice under its key', () => {
    const { result } = renderHook(() => usePulseSort(PULSE_SORT_KEYS.holdings))
    expect(result.current[0]).toBe('attention')
    act(() => result.current[1]('today'))
    expect(result.current[0]).toBe('today')
    expect(localStorage.getItem(PULSE_SORT_KEYS.holdings)).toBe('today')
    expect(localStorage.getItem(PULSE_SORT_KEYS.lists)).toBeNull()
  })

  it('ignores a stored value that is not a sort', () => {
    localStorage.setItem(PULSE_SORT_KEYS.lists, 'sideways')
    const { result } = renderHook(() => usePulseSort(PULSE_SORT_KEYS.lists))
    expect(result.current[0]).toBe('attention')
  })

  it('re-reads when its key changes, and writes under the new key', () => {
    localStorage.setItem(PULSE_SORT_KEYS.lists, 'today')
    const { result, rerender } = renderHook(({ k }) => usePulseSort(k), {
      initialProps: { k: PULSE_SORT_KEYS.holdings as string },
    })
    expect(result.current[0]).toBe('attention')
    rerender({ k: PULSE_SORT_KEYS.lists })
    expect(result.current[0]).toBe('today')
    act(() => result.current[1]('attention'))
    expect(localStorage.getItem(PULSE_SORT_KEYS.lists)).toBe('attention')
    expect(localStorage.getItem(PULSE_SORT_KEYS.holdings)).toBeNull()
  })

  it('still works when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    const { result } = renderHook(() => usePulseSort(PULSE_SORT_KEYS.lists))
    expect(result.current[0]).toBe('attention')
    act(() => result.current[1]('today'))
    expect(result.current[0]).toBe('today')
  })
})
