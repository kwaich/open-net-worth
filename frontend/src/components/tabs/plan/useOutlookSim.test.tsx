// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useOutlookSim } from './useOutlookSim'
import type { SimRequest } from './useOutlookSim'
import type { OutlookInsights, OutlookLevers } from './outlookApi'
import type { LifeResult } from './planData'

type Opts = { quick?: boolean; outcomes?: boolean }
const fetchOutlook = vi.fn<(l: OutlookLevers, o?: Opts) => Promise<unknown>>()
const fetchInsights = vi.fn<(l: OutlookLevers) => Promise<OutlookInsights>>()
vi.mock('./outlookApi', () => ({
  fetchOutlook: (l: OutlookLevers, o?: Opts) => fetchOutlook(l, o),
  fetchInsights: (l: OutlookLevers) => fetchInsights(l),
}))

const fan = (odds: number): { result: LifeResult; outcomes: null } => ({
  result: { successProbability: odds, nPaths: 5000, bands: [] } as unknown as LifeResult,
  outcomes: null,
})
const INSIGHTS: OutlookInsights = {
  earliest: { age: 57, odds: 0.82 },
  extraSave: 500,
  gapSave: null,
  numberAge: 61,
}
const req = (retireAge: number, full = true): SimRequest => ({
  levers: { retireAge },
  chartMode: 'networth',
  full,
})

function deferred<T>() {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => (resolve = r))
  return { promise, resolve }
}
const flush = () => act(async () => void (await Promise.resolve()))

beforeEach(() => {
  fetchOutlook.mockReset()
  fetchInsights.mockReset()
  fetchInsights.mockResolvedValue(INSIGHTS)
})

describe('useOutlookSim', () => {
  it('runs nothing until enabled, then posts the levers and settles on the answer', async () => {
    fetchOutlook.mockResolvedValue(fan(0.7))
    const { result, rerender } = renderHook(
      ({ enabled }) => useOutlookSim(req(60), 'k1', 'p1', enabled),
      { initialProps: { enabled: false } },
    )
    expect(fetchOutlook).not.toHaveBeenCalled()

    rerender({ enabled: true })
    await flush()
    await flush()
    expect(fetchOutlook).toHaveBeenCalledWith({ retireAge: 60 }, { quick: false, outcomes: false })
    expect(result.current.bundle?.result.successProbability).toBe(0.7)
    expect(result.current.settledKey).toBe('k1')
    expect(result.current.pending).toBe(false)
    expect(result.current.bundle?.earliest).toEqual(INSIGHTS.earliest)
  })

  it('reads the saved plan with no levers, as the dashed baseline', async () => {
    fetchOutlook.mockImplementation((levers) =>
      Promise.resolve(fan(Object.keys(levers).length === 0 ? 0.5 : 0.7)),
    )
    const { result } = renderHook(() => useOutlookSim(req(60), 'k1', 'p1', true))
    await flush()
    await flush()
    expect(fetchOutlook).toHaveBeenCalledWith({}, undefined)
    expect(result.current.planResult?.successProbability).toBe(0.5)
  })

  it('collapses rapid changes to the latest while one request is in flight', async () => {
    const slow = deferred<unknown>()
    fetchOutlook.mockImplementation((levers) =>
      Object.keys(levers).length === 0
        ? Promise.resolve(fan(0.5)) // the saved plan
        : levers.retireAge === 60
          ? (slow.promise as Promise<unknown>)
          : Promise.resolve(fan(0.9)),
    )
    const { result, rerender } = renderHook(
      ({ age, key }) => useOutlookSim(req(age), key, 'p1', true),
      { initialProps: { age: 60, key: 'a' } },
    )
    rerender({ age: 61, key: 'b' })
    rerender({ age: 62, key: 'c' })
    rerender({ age: 63, key: 'd' })
    slow.resolve(fan(0.6))
    await flush()
    await flush()
    await flush()
    const sent = fetchOutlook.mock.calls.map(([l]) => l.retireAge).filter((a) => a !== undefined)
    expect(sent).toEqual([60, 63]) // 61 and 62 never went out
    expect(result.current.settledKey).toBe('d')
  })

  it('a drag asks for the quick fan, skips the insights and keeps the last ones', async () => {
    fetchOutlook.mockResolvedValue(fan(0.7))
    const { result, rerender } = renderHook(({ r, key }) => useOutlookSim(r, key, 'p1', true), {
      initialProps: { r: req(60), key: 'a' },
    })
    await flush()
    await flush()
    expect(fetchInsights).toHaveBeenCalledTimes(1)

    rerender({ r: req(58, false), key: 'b' })
    await flush()
    await flush()
    expect(fetchOutlook).toHaveBeenLastCalledWith(
      { retireAge: 58 },
      { quick: true, outcomes: false },
    )
    expect(fetchInsights).toHaveBeenCalledTimes(1)
    expect(result.current.bundle?.earliest).toEqual(INSIGHTS.earliest)
  })

  it('asks for Rich/Broke/Dead only on that chart', async () => {
    fetchOutlook.mockResolvedValue(fan(0.7))
    renderHook(() => useOutlookSim({ ...req(60), chartMode: 'rbd' }, 'k1', 'p1', true))
    await flush()
    expect(fetchOutlook).toHaveBeenCalledWith({ retireAge: 60 }, { quick: false, outcomes: true })
  })

  it('reports a failed run and retries it on request', async () => {
    fetchOutlook.mockRejectedValueOnce(new Error('HTTP 500')) // fan
    fetchOutlook.mockResolvedValue(fan(0.7))
    const { result } = renderHook(() => useOutlookSim(req(60), 'k1', 'p1', true))
    await flush()
    await flush()
    expect(result.current.error).toBe(true)
    expect(result.current.bundle).toBeNull()
    expect(result.current.pending).toBe(false)

    act(() => result.current.retry())
    await flush()
    await flush()
    expect(result.current.error).toBe(false)
    expect(result.current.bundle?.result.successProbability).toBe(0.7)
  })

  it('a failed insights read clears the old safe-to-retire age instead of leaving it up', async () => {
    fetchOutlook.mockResolvedValue(fan(0.7))
    const { result, rerender } = renderHook(({ r, key }) => useOutlookSim(r, key, 'p1', true), {
      initialProps: { r: req(60), key: 'a' },
    })
    await flush()
    await flush()
    expect(result.current.bundle?.earliest).toEqual(INSIGHTS.earliest)
    expect(result.current.error).toBe(false)

    fetchInsights.mockRejectedValueOnce(new Error('HTTP 500'))
    rerender({ r: req(58), key: 'b' })
    await flush()
    await flush()
    await flush()
    expect(result.current.bundle?.earliest).toBeNull()
    expect(result.current.bundle?.result.successProbability).toBe(0.7)
    expect(result.current.error).toBe(true)
  })

  it('a failed saved-plan read drops the baseline line and says so', async () => {
    fetchOutlook.mockImplementation((levers) =>
      Object.keys(levers).length === 0
        ? Promise.reject(new Error('HTTP 500'))
        : Promise.resolve(fan(0.7)),
    )
    const { result } = renderHook(() => useOutlookSim(req(60), 'k1', 'p1', true))
    await flush()
    await flush()
    expect(result.current.planResult).toBeNull()
    expect(result.current.bundle?.result.successProbability).toBe(0.7)
    expect(result.current.error).toBe(true)
  })

  it('a fan that fails after a good one keeps the bundle but reports the error', async () => {
    fetchOutlook.mockResolvedValueOnce(fan(0.7)) // fan
    fetchOutlook.mockResolvedValueOnce(fan(0.5)) // saved plan
    const { result, rerender } = renderHook(({ r, key }) => useOutlookSim(r, key, 'p1', true), {
      initialProps: { r: req(60), key: 'a' },
    })
    await flush()
    await flush()
    fetchOutlook.mockRejectedValueOnce(new Error('HTTP 500'))
    rerender({ r: req(58), key: 'b' })
    await flush()
    await flush()
    expect(result.current.error).toBe(true)
    expect(result.current.settledKey).toBe('a') // the fan on screen is NOT for the current levers
    expect(result.current.pending).toBe(false)
  })
})
