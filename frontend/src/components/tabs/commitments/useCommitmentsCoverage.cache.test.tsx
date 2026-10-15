// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { clearQueryCache } from '../../../state/queryCache'
import { useCommitmentsCoverage } from './useCommitmentsCoverage'

const { authFetch } = vi.hoisted(() => ({ authFetch: vi.fn() }))
vi.mock('../../../api/client', () => ({ authFetch }))

function ok(data: unknown): Response {
  return { ok: true, json: async () => data } as Response
}

describe('useCommitmentsCoverage remount cache', () => {
  beforeEach(() => {
    clearQueryCache()
    authFetch.mockReset()
  })

  it('keeps the last coverage visible while a remount revalidates', async () => {
    const coverage = { verdict: 'on_track', timeline: [] }
    const unmarked = [{ txn_id: 'txn_1' }]
    authFetch.mockResolvedValueOnce(ok(coverage)).mockResolvedValueOnce(ok(unmarked))

    const first = renderHook(() => useCommitmentsCoverage(12))
    await waitFor(() => expect(first.result.current.coverage).toEqual(coverage))
    expect(first.result.current.unmarked).toEqual(unmarked)
    first.unmount()

    let resolveCoverage!: (value: Response) => void
    let resolveUnmarked!: (value: Response) => void
    authFetch
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveCoverage = resolve
          }),
      )
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            resolveUnmarked = resolve
          }),
      )

    const second = renderHook(() => useCommitmentsCoverage(12))
    expect(second.result.current.coverage).toEqual(coverage)
    expect(second.result.current.unmarked).toEqual(unmarked)
    expect(second.result.current.loading).toBe(false)

    await act(async () => {
      resolveCoverage(ok({ ...coverage, at_risk: true, timeline: [] }))
      resolveUnmarked(ok([]))
    })
    await waitFor(() => expect(second.result.current.coverage?.at_risk).toBe(true))
  })
})
