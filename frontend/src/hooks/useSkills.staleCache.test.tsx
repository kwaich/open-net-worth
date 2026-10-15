// @vitest-environment jsdom

import { cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authFetch } from '../api/client'
import { clearQueryCache, setCachedQueryData } from '../state/queryCache'
import { useSkills } from './useSkills'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))

beforeEach(() => {
  clearQueryCache()
  vi.mocked(authFetch).mockReturnValue(new Promise(() => {}))
})

afterEach(() => {
  cleanup()
  clearQueryCache()
  vi.clearAllMocks()
})

describe('useSkills cached response validation', () => {
  it('does not expose a stale object-shaped payload as Skill[] while revalidating', () => {
    setCachedQueryData<unknown>('tab:skills', { skills: [] })

    const { result } = renderHook(() => useSkills())

    expect(result.current.skills).toEqual([])
    expect(Array.isArray(result.current.skills)).toBe(true)
  })
})
