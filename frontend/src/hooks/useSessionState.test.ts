// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useSessionState } from './useSessionState'

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

describe('useSessionState', () => {
  it('starts from what this browser session stored, and writes through', () => {
    sessionStorage.setItem('k', 'a')
    const { result } = renderHook(() => useSessionState('k'))
    expect(result.current[0]).toBe('a')
    act(() => result.current[1]('b'))
    expect([result.current[0], sessionStorage.getItem('k')]).toEqual(['b', 'b'])
    act(() => result.current[1](null))
    expect([result.current[0], sessionStorage.getItem('k')]).toEqual([null, null])
  })

  it('is plain state when storage is unavailable (a locked-down private window)', () => {
    const denied = () => {
      throw new Error('denied')
    }
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(denied)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(denied)
    const { result } = renderHook(() => useSessionState('k'))
    expect(result.current[0]).toBeNull()
    act(() => result.current[1]('x'))
    expect(result.current[0]).toBe('x')
  })
})
