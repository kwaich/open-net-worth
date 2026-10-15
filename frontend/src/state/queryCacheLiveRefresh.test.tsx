// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { emitDataChanged } from '../lucius/bus'
import {
  clearQueryCache,
  getCachedQueryData,
  setCachedQueryData,
  useQueryCache,
} from './queryCache'

describe('query cache live refresh', () => {
  beforeEach(() => clearQueryCache())

  it('revalidates every mounted read on a Lucius write, whatever domain it names', async () => {
    let n = 0
    const fetcher = vi.fn(async () => `v${++n}`)
    const view = renderHook(() => useQueryCache('/api/commitments', fetcher, { staleTime: 60_000 }))
    await waitFor(() => expect(view.result.current.data).toBe('v1'))

    // No key→domain map: a domain the key "doesn't know about" still refreshes it.
    act(() => emitDataChanged(['automations']))

    await waitFor(() => expect(view.result.current.data).toBe('v2'))
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('marks unmounted reads stale so the next mount refetches instead of trusting staleTime', async () => {
    const fetcher = vi.fn(async () => 'fresh')
    setCachedQueryData('/api/payees', 'old')

    act(() => emitDataChanged(['payees']))
    expect(getCachedQueryData('/api/payees')).toBe('old')

    const view = renderHook(() => useQueryCache('/api/payees', fetcher, { staleTime: 60_000 }))
    expect(view.result.current.data).toBe('old')
    await waitFor(() => expect(view.result.current.data).toBe('fresh'))
  })
})
