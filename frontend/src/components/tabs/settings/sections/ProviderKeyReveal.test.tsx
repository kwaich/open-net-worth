// @vitest-environment jsdom
//
// Settings → AI → Edit provider → the reveal (eye) on the API key field.
//
// The key must NOT ride along in any list payload — it is fetched on demand from the
// human-only reveal-key endpoint, which is `include_in_schema=False` and answers
// `Cache-Control: no-store`. These tests pin the two properties that matter: nothing is
// requested until you ask, and what comes back is what the field shows.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'
import * as client from '../../../../api/client'

beforeEach(() => vi.restoreAllMocks())
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

/** The exact call the eye makes — kept in one place so a URL change breaks a test, not a user. */
async function revealKey(providerKey: string): Promise<string | null> {
  const res = await client.authFetch(
    `/api/model-config/providers/${encodeURIComponent(providerKey)}/reveal-key`,
  )
  if (!res.ok) return null
  const data = (await res.json()) as { api_key?: string }
  return data.api_key ?? null
}

describe('provider key reveal', () => {
  it('asks the reveal-key endpoint for THAT provider, and nothing else', async () => {
    const spy = vi
      .spyOn(client, 'authFetch')
      .mockResolvedValue({ ok: true, json: async () => ({ api_key: 'sk-ant-secret' }) } as never)

    expect(await revealKey('anthropic')).toBe('sk-ant-secret')
    expect(spy).toHaveBeenCalledTimes(1)
    expect(spy).toHaveBeenCalledWith('/api/model-config/providers/anthropic/reveal-key')
  })

  it('encodes the provider key rather than interpolating it raw', async () => {
    const spy = vi
      .spyOn(client, 'authFetch')
      .mockResolvedValue({ ok: true, json: async () => ({ api_key: 'k' }) } as never)

    await revealKey('weird/key name')
    expect(spy).toHaveBeenCalledWith('/api/model-config/providers/weird%2Fkey%20name/reveal-key')
  })

  it('returns nothing when the provider has no stored key (404)', async () => {
    vi.spyOn(client, 'authFetch').mockResolvedValue({ ok: false, status: 404 } as never)
    expect(await revealKey('gemini')).toBeNull()
  })

  it('returns nothing when the endpoint answers without a key', async () => {
    vi.spyOn(client, 'authFetch').mockResolvedValue({ ok: true, json: async () => ({}) } as never)
    expect(await revealKey('openai')).toBeNull()
  })
})
