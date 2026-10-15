// @vitest-environment jsdom
//
// The defect this signal exists to fix: the floating dock offered "Ask Lucius" on an instance
// with no provider configured at all — a promise the app cannot keep, and permanently so for
// anyone who declined the model step during first run.

import { describe, it, expect, vi, afterEach } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { useHasAi } from './useHasAi'
import { MODEL_CONFIG_CHANGED_EVENT } from './useModels'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../api/client', () => ({ authFetch }))

afterEach(() => {
  authFetch.mockReset()
})

function respond(body: unknown, ok = true) {
  authFetch.mockImplementation(() => Promise.resolve({ ok, json: () => Promise.resolve(body) }))
}

describe('useHasAi', () => {
  it('reports no AI on an unconfigured instance', async () => {
    respond({ provider: '' })
    const { result } = renderHook(() => useHasAi())
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.hasAi).toBe(false)
  })

  it('reports AI once a provider resolves', async () => {
    respond({ provider: 'claude_cli', model: 'sonnet' })
    const { result } = renderHook(() => useHasAi())
    await waitFor(() => expect(result.current.hasAi).toBe(true))
  })

  it('treats an unreachable backend as no AI rather than throwing', async () => {
    authFetch.mockImplementation(() => Promise.reject(new Error('offline')))
    const { result } = renderHook(() => useHasAi())
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.hasAi).toBe(false)
  })

  it('treats a non-OK response as no AI', async () => {
    respond({}, false)
    const { result } = renderHook(() => useHasAi())
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.hasAi).toBe(false)
  })

  it('re-reads when the model config changes, so first run updates the UI live', async () => {
    respond({ provider: '' })
    const { result } = renderHook(() => useHasAi())
    await waitFor(() => expect(result.current.loaded).toBe(true))
    expect(result.current.hasAi).toBe(false)

    respond({ provider: 'anthropic' })
    window.dispatchEvent(new Event(MODEL_CONFIG_CHANGED_EVENT))

    await waitFor(() => expect(result.current.hasAi).toBe(true))
  })

  it('does not claim a verdict before the first answer arrives', () => {
    respond({ provider: 'anthropic' })
    const { result } = renderHook(() => useHasAi())
    expect(result.current.loaded).toBe(false)
  })
})
