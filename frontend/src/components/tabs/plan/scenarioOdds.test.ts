// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useScenarioOdds } from './scenarioOdds'
import type { SavedScenario } from './usePlanState'

const fetchScenarioOdds = vi.fn<() => Promise<Record<string, number>>>()
vi.mock('./outlookApi', () => ({ fetchScenarioOdds: () => fetchScenarioOdds() }))

const SCENARIO = {
  id: 's1',
  name: 'Retire at 58',
  retireAge: 58,
  odds: 12,
  successPct: 12,
} as unknown as SavedScenario

beforeEach(() => {
  vi.useFakeTimers()
  fetchScenarioOdds.mockReset()
  fetchScenarioOdds.mockResolvedValue({ s1: 64 })
})
afterEach(() => vi.useRealTimers())

describe('useScenarioOdds', () => {
  it('reads the live odds from the backend engine, not the stored snapshot', async () => {
    const { result } = renderHook(() => useScenarioOdds([SCENARIO], 'ctx', true))
    expect(result.current).toEqual({})
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(result.current).toEqual({ s1: 64 })
  })

  it('asks nothing while disabled or with no scenarios', async () => {
    renderHook(() => useScenarioOdds([SCENARIO], 'ctx', false))
    renderHook(() => useScenarioOdds([], 'ctx', true))
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000)
    })
    expect(fetchScenarioOdds).not.toHaveBeenCalled()
  })

  it('re-reads when the saved plan behind the scenarios moves, once per burst', async () => {
    const { rerender } = renderHook(({ ctx }) => useScenarioOdds([SCENARIO], ctx, true), {
      initialProps: { ctx: 'a' },
    })
    rerender({ ctx: 'b' })
    rerender({ ctx: 'c' })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })
    expect(fetchScenarioOdds).toHaveBeenCalledTimes(1)
  })
})
