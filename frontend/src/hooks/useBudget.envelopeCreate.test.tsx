// @vitest-environment jsdom
//
// RFC envelope⊥category full split §Backend contract item 1 / §Frontend: "Add envelope" (the
// group + / group-menu flow) now POSTs a PURE envelope to /api/planning/envelopes — it must
// never fall back to the old create-a-category path.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { useBudget } from './useBudget'

vi.mock('../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

const EMPTY_MONTH = {
  month: '2026-07',
  base_currency: 'SGD',
  ready_to_assign: 0,
  groups: [],
  totals: { assigned: 0, activity: 0, available: 0, underfunded: 0 },
  nav: { current: '2026-07', prev: '2026-06', earliest: '2026-01', can_go_back: true },
  assigned_categories: [],
}

const CREATED_ENVELOPE = {
  id: 'env-new',
  name: 'Emergency fund',
  group_id: 'g1',
  order: 3,
  category_ids: [],
}

function mockRoutes() {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(EMPTY_MONTH))
    if (url.startsWith('/api/planning/health')) return Promise.resolve(okJson(null))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/planning/notes')) return Promise.resolve(okJson({ notes: {} }))
    if (url === '/api/planning/sync-commitment-categories') return Promise.resolve(okJson({}))
    if (url === '/api/planning/envelopes' && opts?.method === 'POST') {
      return Promise.resolve(okJson(CREATED_ENVELOPE))
    }
    return Promise.resolve(okJson({}))
  })
}

beforeEach(() => {
  mockAuthFetch.mockReset()
  mockRoutes()
})
afterEach(() => cleanup())

async function mountedBudget() {
  const rendered = renderHook(() => useBudget('2026-07'))
  await waitFor(() => expect(rendered.result.current.loading).toBe(false))
  mockAuthFetch.mockClear()
  return rendered
}

describe('useBudget.createEnvelope — "Add envelope" server call', () => {
  it('POSTs to /api/planning/envelopes with {name, group_id}', async () => {
    const { result } = await mountedBudget()

    await act(async () => {
      await result.current.createEnvelope('Emergency fund', 'g1')
    })

    expect(mockAuthFetch).toHaveBeenCalledWith('/api/planning/envelopes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Emergency fund', group_id: 'g1' }),
    })
  })

  it('omits group_id from the body when none is given', async () => {
    const { result } = await mountedBudget()

    await act(async () => {
      await result.current.createEnvelope('Emergency fund')
    })

    expect(mockAuthFetch).toHaveBeenCalledWith('/api/planning/envelopes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Emergency fund' }),
    })
  })

  it('resolves with the created envelope and refreshes the month table', async () => {
    const { result } = await mountedBudget()

    let created: { id: string } | undefined
    await act(async () => {
      created = await result.current.createEnvelope('Emergency fund', 'g1')
    })

    expect(created?.id).toBe('env-new')
    expect(mockAuthFetch).toHaveBeenCalledWith(
      expect.stringMatching(/^\/api\/planning\/month/),
      expect.anything(),
    )
  })

  it('never falls back to POST /api/categories — a pure envelope, no category', async () => {
    const { result } = await mountedBudget()

    await act(async () => {
      await result.current.createEnvelope('Emergency fund', 'g1')
    })

    const postedCategory = mockAuthFetch.mock.calls.some(
      ([url, opts]) =>
        url === '/api/categories' && (opts as RequestInit | undefined)?.method === 'POST',
    )
    expect(postedCategory).toBe(false)
  })
})
