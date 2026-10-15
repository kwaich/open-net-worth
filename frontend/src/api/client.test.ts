// Regression test (2026-07-27 perf investigation): dozens of components each independently
// authFetch the same read endpoint with no shared cache, so a single page load fires the
// SAME GET concurrently 2-6x — measured directly causing multi-second stalls on a
// single-process backend. authFetch now collapses concurrent identical plain GETs into one
// real network request, giving every caller its own `res.clone()`.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { authFetch } from './client'

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('authFetch GET de-duplication', () => {
  it('collapses two concurrent identical GETs into one real fetch', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    const [a, b] = await Promise.all([authFetch('/api/profile'), authFetch('/api/profile')])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    await expect(a.json()).resolves.toEqual({ ok: true })
    await expect(b.json()).resolves.toEqual({ ok: true })
  })

  it('gives each waiter an independently-readable response body', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ n: 1 }))

    const results = await Promise.all([
      authFetch('/api/accounts'),
      authFetch('/api/accounts'),
      authFetch('/api/accounts'),
    ])

    // Every single one must still be able to read its own body — proves clone(), not
    // the same Response object handed to all three (a body can only be read once).
    for (const res of results) {
      await expect(res.json()).resolves.toEqual({ n: 1 })
    }
  })

  it('does NOT dedupe once the prior call has already settled — no stale caching', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    await authFetch('/api/profile')
    await authFetch('/api/profile')

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does NOT dedupe POST requests', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))

    await Promise.all([
      authFetch('/api/transactions', { method: 'POST', body: '{}' }),
      authFetch('/api/transactions', { method: 'POST', body: '{}' }),
    ])

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does NOT dedupe a GET carrying a custom AbortSignal', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }))
    const controller1 = new AbortController()
    const controller2 = new AbortController()

    await Promise.all([
      authFetch('/api/profile', { signal: controller1.signal }),
      authFetch('/api/profile', { signal: controller2.signal }),
    ])

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not leave a failed request stuck in the in-flight map', async () => {
    fetchMock.mockRejectedValueOnce(new Error('network down'))
    fetchMock.mockResolvedValueOnce(jsonResponse({ ok: true }))

    await expect(authFetch('/api/profile')).rejects.toThrow('network down')
    // A fresh call after the failure must retry, not stay wedged on the rejected promise.
    await expect(authFetch('/api/profile')).resolves.toBeDefined()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('dedupes different concurrent URLs independently', async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(jsonResponse({ url })))

    await Promise.all([
      authFetch('/api/profile'),
      authFetch('/api/profile'),
      authFetch('/api/accounts'),
      authFetch('/api/accounts'),
    ])

    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('authFetch 401 handling', () => {
  it('hands the gateway reauth URL to the unauthorized handler', async () => {
    const { setUnauthorizedHandler } = await import('./client')
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ reauth: 'https://cloud.example/connect?next=/x' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    const res = await authFetch('/api/profile', { method: 'POST', body: '{}' })
    expect(res.status).toBe(401)
    expect(handler).toHaveBeenCalledWith('https://cloud.example/connect?next=/x')
    setUnauthorizedHandler(null)
  })

  it('calls the handler without a URL for an ordinary 401', async () => {
    const { setUnauthorizedHandler } = await import('./client')
    const handler = vi.fn()
    setUnauthorizedHandler(handler)
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: 'authentication required' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    await authFetch('/api/profile', { method: 'POST', body: '{}' })
    expect(handler).toHaveBeenCalledWith(undefined)
    setUnauthorizedHandler(null)
  })
})
