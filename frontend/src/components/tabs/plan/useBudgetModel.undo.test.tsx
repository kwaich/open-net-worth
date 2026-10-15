// @vitest-environment jsdom
//
// Undo pill (handoff delta #11) — SERVER-BACKED inverses (never a client-state snapshot, which
// would be a lie after any refresh). Each test drives useBudgetModel() against a mocked
// authFetch and asserts the EXACT inverse call(s) doUndo() fires — not just "something happened".
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { useBudgetModel, RTA_KEY } from './useBudgetModel'
import { fSmart } from './planData'
import { clearQueryCache } from '../../../state/queryCache'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}
const noop = () => vi.fn() as (msg: string) => void

const MONTH_DATA = {
  month: '2026-07',
  base_currency: 'SGD',
  ready_to_assign: 500,
  groups: [
    {
      id: 'g1',
      name: 'Everyday',
      collapsed: false,
      categories: [
        {
          key: 'env-groceries',
          label: 'Groceries',
          envelope_id: 'env-groceries',
          category_ids: ['groceries'],
          color: '#3b86dd',
          assigned: 100,
          activity: -20,
          available: 80,
          target: null,
          needed_this_month: 0,
          status: 'none',
        },
        {
          key: 'env-dining',
          label: 'Dining',
          envelope_id: 'env-dining',
          category_ids: ['dining'],
          color: '#e0a032',
          assigned: 20,
          activity: 0,
          available: 20,
          target: null,
          needed_this_month: 0,
          status: 'none',
        },
      ],
      subtotal: { assigned: 120, activity: -20, available: 100 },
    },
  ],
  totals: { assigned: 120, activity: -20, available: 100, underfunded: 0 },
  nav: { current: '2026-07', prev: '2026-06', earliest: '2026-01', can_go_back: true },
  assigned_categories: ['groceries', 'dining'],
}

function mockRoutes() {
  mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
    const method = opts?.method ?? 'GET'
    if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(MONTH_DATA))
    if (url.startsWith('/api/planning/health')) return Promise.resolve(okJson(null))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/planning/notes')) return Promise.resolve(okJson({ notes: {} }))
    if (url === '/api/planning/sync-commitment-categories') return Promise.resolve(okJson({}))
    if (url === '/api/planning/assign') return Promise.resolve(okJson({ ok: true }))
    if (url === '/api/planning/move') return Promise.resolve(okJson({ ok: true }))
    if (url === '/api/planning/apply') return Promise.resolve(okJson({ ok: true }))
    if (url === '/api/planning/auto-assign-preview')
      return Promise.resolve(okJson({ steps: [], total_delta: 0 }))
    if (url === '/api/planning/envelopes/undo-delete' && method === 'POST')
      return Promise.resolve(okJson({}))
    if (/^\/api\/planning\/envelopes\/[^/]+\/split$/.test(url) && method === 'POST')
      return Promise.resolve(okJson({ ok: true }))
    if (/^\/api\/planning\/envelopes\/[^/]+\/add-category$/.test(url) && method === 'POST')
      return Promise.resolve(okJson({ ok: true }))
    if (/^\/api\/planning\/envelopes\/[^/?]+(\?.*)?$/.test(url) && method === 'DELETE') {
      return Promise.resolve(
        okJson({
          ok: true,
          undo_token: 'edu-test-token',
          former_category_ids: ['groceries'],
          zeroed_assignments: { '2026-07': 80, '2026-06': 50 },
          month: '2026-07',
          available: 80,
          delta_rta: 80,
        }),
      )
    }
    if (url === '/api/planning/envelopes/merge') return Promise.resolve(okJson({ ok: true }))
    return Promise.resolve(okJson({}))
  })
}

// The hook's month defaults to the WALL-CLOCK month (useBudget -> thisMonth()), so every
// fixture literal in this file is only correct while "now" falls inside 2026-07. Left to the
// real clock this suite goes red on the 1st of the next month with no code change — which is
// exactly what happened. Pin the clock instead of chasing the literals.
// `shouldAdvanceTime` keeps real timers ticking so waitFor still resolves.
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-07-15T12:00:00Z'))
  clearQueryCache()
  mockAuthFetch.mockReset()
  mockRoutes()
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

async function mountedModel() {
  const rendered = renderHook(() => useBudgetModel())
  await waitFor(() => expect(rendered.result.current.loading).toBe(false))
  mockAuthFetch.mockClear()
  return rendered
}

describe('useBudgetModel undo — assign', () => {
  it('captures "Assigned $X" and undo replays the PREVIOUS assigned value', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.assignTo('env-groceries', 150, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Assigned S$50'))
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/assign',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ month: '2026-07', envelope_id: 'env-groceries', amount: 100 }),
        }),
      ),
    )
  })

  it('a release (lower assigned) captures "Released $X"', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.assignTo('env-groceries', 60, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Released S$40'))
  })
})

describe('useBudgetModel undo — move/cover (incl. __rta ends)', () => {
  it('a plain envelope→envelope move undoes as the MIRRORED move', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.moveBetweenEnvelopes('env-groceries', 'env-dining', 30, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Moved S$30'))
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/move',
        expect.objectContaining({
          body: JSON.stringify({
            month: '2026-07',
            from_envelope_id: 'env-dining',
            to_envelope_id: 'env-groceries',
            amount: 30,
          }),
        }),
      ),
    )
  })

  it('a release TO Ready to Assign undoes as fund-FROM-RTA (mirrored)', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.moveBetweenEnvelopes('env-groceries', RTA_KEY, 20, noop())
    })
    await waitFor(() => expect(result.current.undo).toBeTruthy())
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/move',
        expect.objectContaining({
          body: JSON.stringify({
            month: '2026-07',
            from_envelope_id: RTA_KEY,
            to_envelope_id: 'env-groceries',
            amount: 20,
          }),
        }),
      ),
    )
  })

  it('a fund FROM Ready to Assign undoes as release-TO-RTA (mirrored)', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.moveBetweenEnvelopes(RTA_KEY, 'env-dining', 15, noop())
    })
    await waitFor(() => expect(result.current.undo).toBeTruthy())
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/move',
        expect.objectContaining({
          body: JSON.stringify({
            month: '2026-07',
            from_envelope_id: 'env-dining',
            to_envelope_id: RTA_KEY,
            amount: 15,
          }),
        }),
      ),
    )
  })

  // FEB-01 — the exact repro: a server-REFUSED move (e.g. releasing more than is assigned)
  // must never leave a "working" undo pill. Before the fix, capture happened BEFORE the
  // request, so clicking Undo after a refusal fired a real, uncapped money move the user never
  // asked for. Now the pill only arms on the server's OK.
  it('a REFUSED move (server rejects) never arms the undo pill', async () => {
    const { result } = await mountedModel()
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/planning/move')
        return Promise.resolve({
          ok: false,
          status: 422,
          json: async () => ({ detail: 'cannot move more than is assigned' }),
        } as Response)
      if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(MONTH_DATA))
      if (url.startsWith('/api/planning/health')) return Promise.resolve(okJson(null))
      return Promise.resolve(okJson({}))
    })
    const toast = vi.fn()
    act(() => {
      result.current.moveBetweenEnvelopes('env-groceries', RTA_KEY, 150, toast)
    })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('cannot move more than is assigned'))
    expect(result.current.undo).toBeNull()
  })
})

describe('useBudgetModel undo — auto-assign apply', () => {
  it('replays every affected row back to its PRE-apply `cur` value', async () => {
    const { result } = await mountedModel()
    const prev = {
      label: 'Underfunded',
      steps: [
        {
          key: 'env-groceries',
          label: 'Groceries',
          color: '#3b86dd',
          cur: 100,
          next: 150,
          delta: 50,
        },
      ],
      total: 50,
    }
    act(() => {
      result.current.applyAutoPreview(prev, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Auto-assign • S$50'))
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/assign',
        expect.objectContaining({
          body: JSON.stringify({ month: '2026-07', envelope_id: 'env-groceries', amount: 100 }),
        }),
      ),
    )
  })

  // BUD-SWEEP-01 (frontend half) — a CLIENT mode (Underfunded/Cover all
  // overspending/Reset) has no server-side preview call, so its steps only exist locally: they
  // must replay as individual sequential /assign calls (never the bulk endpoint, which would
  // let the backend recompute its OWN steps and silently diverge from what the modal showed).
  it('a CLIENT mode (no HISTORY_AUTO_MODES entry) applies via individual /assign calls, never the bulk endpoint', async () => {
    const { result } = await mountedModel()
    const prev = {
      label: 'Underfunded',
      steps: [
        {
          key: 'env-groceries',
          label: 'Groceries',
          color: '#3b86dd',
          cur: 100,
          next: 150,
          delta: 50,
        },
        { key: 'env-dining', label: 'Dining', color: '#e0a032', cur: 20, next: 40, delta: 20 },
      ],
      total: 70,
    }
    act(() => {
      result.current.applyAutoPreview(prev, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Auto-assign • S$70'))
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/planning/assign',
      expect.objectContaining({
        body: JSON.stringify({ month: '2026-07', envelope_id: 'env-groceries', amount: 150 }),
      }),
    )
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/planning/assign',
      expect.objectContaining({
        body: JSON.stringify({ month: '2026-07', envelope_id: 'env-dining', amount: 40 }),
      }),
    )
    expect(mockAuthFetch).not.toHaveBeenCalledWith('/api/planning/apply', expect.anything())
  })

  // BUD-SWEEP-01 (frontend half) — a SERVER (history) mode's steps came FROM
  // auto-assign-preview; applying must go through the SAME single bulk apply_budget_change step
  // the backend uses (never a per-row assign fan-out), so the confirm can't race/clobber itself
  // the way the pre-2af27c14 unlocked store did.
  it('a SERVER (history) mode applies via ONE bulk POST /api/planning/apply, not a per-row fan-out', async () => {
    const { result } = await mountedModel()
    const prev = {
      label: 'Assigned Last Month',
      steps: [
        {
          key: 'env-groceries',
          label: 'Groceries',
          color: '#3b86dd',
          cur: 100,
          next: 150,
          delta: 50,
        },
        { key: 'env-dining', label: 'Dining', color: '#e0a032', cur: 20, next: 40, delta: 20 },
      ],
      total: 70,
    }
    act(() => {
      result.current.applyAutoPreview(prev, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Auto-assign • S$70'))
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/planning/apply',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          type: 'auto_assign',
          month: '2026-07',
          mode: 'assigned_last_month',
        }),
      }),
    )
    expect(mockAuthFetch).not.toHaveBeenCalledWith('/api/planning/assign', expect.anything())
  })

  // FEB-01 — the undo pill only arms once the forward write actually lands; a failed apply
  // must never offer an undo for money that was never moved.
  it('a FAILED apply refreshes and toasts an error, but never arms the undo pill', async () => {
    const { result } = await mountedModel()
    mockAuthFetch.mockImplementation((url: string) => {
      if (url === '/api/planning/apply')
        return Promise.resolve({
          ok: false,
          status: 422,
          json: async () => ({ detail: 'nope' }),
        } as Response)
      if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(MONTH_DATA))
      if (url.startsWith('/api/planning/health')) return Promise.resolve(okJson(null))
      return Promise.resolve(okJson({}))
    })
    const toast = vi.fn()
    const prev = {
      label: 'Assigned Last Month',
      steps: [
        {
          key: 'env-groceries',
          label: 'Groceries',
          color: '#3b86dd',
          cur: 100,
          next: 150,
          delta: 50,
        },
      ],
      total: 50,
    }
    act(() => {
      result.current.applyAutoPreview(prev, toast)
    })
    await waitFor(() => expect(toast).toHaveBeenCalledWith('nope'))
    expect(result.current.undo).toBeNull()
  })
})

describe('useBudgetModel undo — unlink / link', () => {
  it('unlink (splitCategory) undoes by re-linking to the SAME source envelope', async () => {
    const { result } = await mountedModel()
    await act(async () => {
      await result.current.splitCategory(
        'env-groceries',
        'groceries',
        'Groceries',
        'Groceries',
        noop(),
      )
    })
    expect(result.current.undo?.label).toBe('Groceries unlinked')
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/envelopes/env-groceries/add-category',
        expect.objectContaining({
          body: JSON.stringify({ category_id: 'groceries' }),
        }),
      ),
    )
  })

  it('link (linkCategoryToEnvelope, from Unbudgeted) undoes by splitting back out', async () => {
    const { result } = await mountedModel()
    await act(async () => {
      await result.current.linkCategoryToEnvelope(
        'misc',
        'env-groceries',
        'Misc',
        'Groceries',
        noop(),
      )
    })
    expect(result.current.undo?.label).toBe('Misc linked')
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/envelopes/env-groceries/split',
        expect.objectContaining({
          body: JSON.stringify({ category_id: 'misc' }),
        }),
      ),
    )
  })
})

describe('useBudgetModel undo — addCategoriesToEnvelope (sole-member merge scope)', () => {
  it('a batch of only unlink+link (non-sole) picks undoes each: split from target, re-link to original source', async () => {
    const { result } = await mountedModel()
    const picks = [{ categoryId: 'misc', sourceEnvelopeId: 'env-misc-multi', soleMember: false }]
    await act(async () => {
      await result.current.addCategoriesToEnvelope('env-groceries', 'Groceries', picks, noop())
    })
    expect(result.current.undo?.label).toBe('Categories added')
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/envelopes/env-groceries/split',
        expect.objectContaining({
          body: JSON.stringify({ category_id: 'misc' }),
        }),
      ),
    )
    expect(mockAuthFetch).toHaveBeenCalledWith(
      '/api/planning/envelopes/env-misc-multi/add-category',
      expect.objectContaining({
        body: JSON.stringify({ category_id: 'misc' }),
      }),
    )
  })

  it('a batch with ANY sole-member (merge-based) pick offers NO undo pill at all', async () => {
    const { result } = await mountedModel()
    const picks = [
      { categoryId: 'misc', sourceEnvelopeId: 'env-misc-multi', soleMember: false },
      { categoryId: 'solo', sourceEnvelopeId: 'env-solo', soleMember: true },
    ]
    await act(async () => {
      await result.current.addCategoriesToEnvelope('env-groceries', 'Groceries', picks, noop())
    })
    expect(result.current.undo).toBeNull()
  })
})

describe('useBudgetModel undo — delete / restore envelope', () => {
  it('delete undoes by replaying the server-held undo_token in ONE call', async () => {
    const { result } = await mountedModel()
    await act(async () => {
      await result.current.deleteEnvelope('env-groceries', 'Groceries', noop())
    })
    expect(result.current.undo?.label).toBe('Envelope deleted')
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/envelopes/undo-delete',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ token: 'edu-test-token' }),
        }),
      ),
    )
    // The client never replays zeroed_assignments itself any more — the server does, atomically,
    // as part of the SAME undo write.
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringMatching(/\/restore$/),
      expect.anything(),
    )
    expect(mockAuthFetch).not.toHaveBeenCalledWith('/api/planning/apply', expect.anything())
  })

  // ADR 2026-09-10: deleting an overspent envelope does NOT end the hold — its members are
  // detached, and an unlinked category's cash spend holds the same hole in
  // `held_outside_envelopes` until the month-end roll. So Ready to Assign is not charged now.
  // (Verified against `services.envelopes.delete_envelope`: assign 100 / spend 300, available
  // −200, RTA 900 → delete → held_outside_envelopes −300, RTA 1000; July still 700.) The
  // 2026-09-09 wording said the delete charged the hole immediately, which is no longer true.
  // The toast now reads the SERVER's own realized `available`/`delta_rta` (the DELETE
  // response's echo of `envelope_delete_preview`'s figure) — never a client-precomputed number,
  // which undercounts (docs/launch/BOARD.md's repro).
  it('deleting an envelope in the red says the overspend is still held', async () => {
    const { result } = await mountedModel()
    const red = vi.fn()
    // available < 0 and delta_rta ~ 0: nothing was assigned this month, so nothing releases —
    // the whole hole just changes hands (still held), matching the ORIGINAL worked case.
    mockAuthFetch.mockImplementationOnce(() =>
      Promise.resolve(
        okJson({
          ok: true,
          undo_token: 'edu-test-token',
          former_category_ids: ['groceries'],
          zeroed_assignments: {},
          month: '2026-07',
          available: -50,
          delta_rta: 0,
        }),
      ),
    )
    await act(async () => {
      await result.current.deleteEnvelope('env-groceries', 'Groceries', red)
    })
    expect(red).toHaveBeenCalledWith(
      `Envelope “Groceries” deleted — its ${fSmart(50)} overspend is still held until month-end`,
    )
    // control: a positive delta_rta still says where the money went
    const funded = vi.fn()
    mockAuthFetch.mockImplementationOnce(() =>
      Promise.resolve(
        okJson({
          ok: true,
          undo_token: 'edu-test-token-2',
          former_category_ids: ['dining'],
          zeroed_assignments: { '2026-07': 20 },
          month: '2026-07',
          available: 20,
          delta_rta: 20,
        }),
      ),
    )
    await act(async () => {
      await result.current.deleteEnvelope('env-dining', 'Dining', funded)
    })
    expect(funded).toHaveBeenCalledWith(
      `Envelope “Dining” deleted — ${fSmart(20)} returned to Ready to Assign`,
    )
  })
})

describe('useBudgetModel undo — merge is NOT one-shot-undoable (RFC decision)', () => {
  it('merging two envelopes never offers an undo pill', async () => {
    const { result } = await mountedModel()
    await act(async () => {
      await result.current.mergeEnvelope(
        'env-dining',
        'env-groceries',
        'Dining',
        'Groceries',
        noop(),
      )
    })
    expect(result.current.undo).toBeNull()
  })
})

describe('useBudgetModel undo — FEB-08: month navigation clears the pill', () => {
  it('navigating to next month clears an armed undo pill (never repaints the wrong month)', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.assignTo('env-groceries', 150, noop())
    })
    await waitFor(() => expect(result.current.undo).toBeTruthy())
    act(() => {
      result.current.onNext()
    })
    expect(result.current.undo).toBeNull()
  })

  it('navigating to the previous month also clears the pill', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.assignTo('env-groceries', 150, noop())
    })
    await waitFor(() => expect(result.current.undo).toBeTruthy())
    act(() => {
      result.current.onPrev()
    })
    expect(result.current.undo).toBeNull()
  })
})

describe('useBudgetModel — ENV-RES-05: hideGroup skips zero-category envelope rows', () => {
  const MIXED_GROUP_DATA = {
    ...MONTH_DATA,
    groups: [
      {
        id: 'g1',
        name: 'Everyday',
        collapsed: false,
        categories: [
          {
            key: 'env-groceries',
            label: 'Groceries',
            envelope_id: 'env-groceries',
            category_ids: ['groceries'],
            color: '#3b86dd',
            assigned: 100,
            activity: -20,
            available: 80,
            target: null,
            needed_this_month: 0,
            status: 'none',
          },
          {
            key: 'env-empty',
            label: 'Emergency fund',
            envelope_id: 'env-empty',
            category_ids: [],
            color: '#9a7fd0',
            assigned: 0,
            activity: 0,
            available: 0,
            target: null,
            needed_this_month: 0,
            status: 'none',
          },
        ],
        subtotal: { assigned: 100, activity: -20, available: 80 },
      },
    ],
  }

  it('hiding a group with a zero-category row hides only the category-backed member and toasts success', async () => {
    mockAuthFetch.mockImplementation((url: string, opts?: RequestInit) => {
      const method = opts?.method ?? 'GET'
      if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(MIXED_GROUP_DATA))
      if (url.startsWith('/api/planning/health')) return Promise.resolve(okJson(null))
      if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/planning/notes')) return Promise.resolve(okJson({ notes: {} }))
      if (url === '/api/planning/sync-commitment-categories') return Promise.resolve(okJson({}))
      if (url === '/api/categories/groceries/hide' && method === 'POST')
        return Promise.resolve(okJson({ ok: true }))
      return Promise.resolve(okJson({}))
    })
    const { result } = await mountedModel()
    const toast = vi.fn()
    act(() => {
      result.current.hideGroup('g1', toast)
    })
    expect(toast).toHaveBeenCalledWith('Group “Everyday” hidden')
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/categories/groceries/hide',
        expect.objectContaining({ method: 'POST' }),
      ),
    )
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      '/api/categories/env-empty/hide',
      expect.anything(),
    )
  })

  it('a group that is ONLY zero-category envelopes cannot be hidden — toasts instead of 404ing', async () => {
    const ONLY_EMPTY_DATA = {
      ...MONTH_DATA,
      groups: [
        {
          id: 'g1',
          name: 'Goals',
          collapsed: false,
          categories: [
            {
              key: 'env-empty',
              label: 'Emergency fund',
              envelope_id: 'env-empty',
              category_ids: [],
              color: '#9a7fd0',
              assigned: 0,
              activity: 0,
              available: 0,
              target: null,
              needed_this_month: 0,
              status: 'none',
            },
          ],
          subtotal: { assigned: 0, activity: 0, available: 0 },
        },
      ],
    }
    mockAuthFetch.mockImplementation((url: string) => {
      if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(ONLY_EMPTY_DATA))
      if (url.startsWith('/api/planning/health')) return Promise.resolve(okJson(null))
      if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
      if (url.startsWith('/api/planning/notes')) return Promise.resolve(okJson({ notes: {} }))
      if (url === '/api/planning/sync-commitment-categories') return Promise.resolve(okJson({}))
      return Promise.resolve(okJson({}))
    })
    const { result } = await mountedModel()
    const toast = vi.fn()
    mockAuthFetch.mockClear()
    act(() => {
      result.current.hideGroup('g1', toast)
    })
    expect(toast).toHaveBeenCalledWith(expect.stringContaining('has no categories'))
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      expect.stringContaining('/hide'),
      expect.anything(),
    )
  })
})

describe('useBudgetModel undo — one-shot semantics', () => {
  it('doUndo consumes the pill — a second call is a no-op', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.assignTo('env-groceries', 150, noop())
    })
    await waitFor(() => expect(result.current.undo).toBeTruthy())
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() => expect(result.current.undo).toBeNull())
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await new Promise((r) => setTimeout(r, 0))
    expect(mockAuthFetch).not.toHaveBeenCalled()
  })

  it('a fresh capture replaces the pending one (only the LATEST op is undoable)', async () => {
    const { result } = await mountedModel()
    act(() => {
      result.current.assignTo('env-groceries', 150, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Assigned S$50'))
    act(() => {
      result.current.assignTo('env-dining', 40, noop())
    })
    await waitFor(() => expect(result.current.undo?.label).toBe('Assigned S$20'))
    mockAuthFetch.mockClear()
    act(() => {
      result.current.doUndo(noop())
    })
    await waitFor(() =>
      expect(mockAuthFetch).toHaveBeenCalledWith(
        '/api/planning/assign',
        expect.objectContaining({
          body: JSON.stringify({ month: '2026-07', envelope_id: 'env-dining', amount: 20 }),
        }),
      ),
    )
    expect(mockAuthFetch).not.toHaveBeenCalledWith(
      '/api/planning/assign',
      expect.objectContaining({
        body: JSON.stringify({ month: '2026-07', envelope_id: 'env-groceries', amount: 100 }),
      }),
    )
  })
})
