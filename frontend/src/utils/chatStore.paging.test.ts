import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ authFetch: vi.fn(), setUnauthorizedHandler: vi.fn() }))

import { authFetch } from '../api/client'
import { fetchEarlierMessages, fetchToolDetail, positionalCounter } from './chatStore'

const CONV = 'abc-123-def'

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

beforeEach(() => {
  vi.mocked(authFetch).mockReset()
})

describe('positionalCounter', () => {
  it('parses the counter off a transcript-rehydrated id', () => {
    expect(positionalCounter(CONV, `${CONV}-31`)).toBe(31)
    expect(positionalCounter(CONV, `${CONV}-1`)).toBe(1)
  })

  it('rejects ids from other conversations and live-born ids', () => {
    expect(positionalCounter(CONV, 'other-conv-31')).toBeNull()
    // live messages mint uuids — no server position
    expect(positionalCounter(CONV, '550e8400-e29b-41d4-a716-446655440000')).toBeNull()
    expect(positionalCounter(CONV, `${CONV}-img-3-0`)).toBeNull()
  })
})

describe('fetchEarlierMessages', () => {
  it('pages with before=<earliest counter> and the default tail', async () => {
    vi.mocked(authFetch).mockResolvedValue(okJson({ messages: [{ id: `${CONV}-30` }], total: 90 }))

    const out = await fetchEarlierMessages(CONV, `${CONV}-31`)

    expect(out).toEqual([{ id: `${CONV}-30` }])
    expect(vi.mocked(authFetch)).toHaveBeenCalledWith(
      `/api/sessions/${CONV}/messages?tail=30&before=31`,
    )
  })

  it('returns null without fetching when already at the first message', async () => {
    expect(await fetchEarlierMessages(CONV, `${CONV}-1`)).toBeNull()
    expect(vi.mocked(authFetch)).not.toHaveBeenCalled()
  })

  it('returns null without fetching for a live-born earliest id', async () => {
    expect(await fetchEarlierMessages(CONV, 'not-positional')).toBeNull()
    expect(vi.mocked(authFetch)).not.toHaveBeenCalled()
  })
})

describe('fetchToolDetail', () => {
  it('fetches the full block for an elided tool call', async () => {
    vi.mocked(authFetch).mockResolvedValue(okJson({ name: 'fetch_page', result: 'big' }))

    const detail = await fetchToolDetail(CONV, 'toolu_01AB')

    expect(detail).toEqual({ name: 'fetch_page', result: 'big' })
    expect(vi.mocked(authFetch)).toHaveBeenCalledWith(
      `/api/sessions/${CONV}/tool-results/toolu_01AB`,
    )
  })

  it('returns null on a 404 instead of throwing', async () => {
    vi.mocked(authFetch).mockResolvedValue({ ok: false } as Response)
    expect(await fetchToolDetail(CONV, 'toolu_gone')).toBeNull()
  })
})
