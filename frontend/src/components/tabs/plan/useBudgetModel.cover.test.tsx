// @vitest-environment jsdom
//
// The one-click "Cover $X" action, 2026-09-09. Under the restored YNAB identity (ADR
// 2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category) covering an overspent
// envelope COSTS Ready to Assign dollar for dollar — the exact reverse of the one-day
// 2026-09-08 rule, where covering was free. The button still fires with no confirm step (it is
// undoable, and a dialog on every cover would cost more than a sentence does), so the TOAST is
// the surface that has to name the price. A generic "Covered <envelope> • $X" does not.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, waitFor, cleanup } from '@testing-library/react'
import { useBudgetModel } from './useBudgetModel'
import { clearQueryCache } from '../../../state/queryCache'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn() }))
import { authFetch } from '../../../api/client'
const mockAuthFetch = authFetch as ReturnType<typeof vi.fn>

function okJson(body: unknown) {
  return { ok: true, json: async () => body } as Response
}

// Dining is S$45.50 in the hole; the identity closes at 500 = 1000 − 500.
const MONTH_DATA = {
  month: '2026-07',
  ready_to_assign: 500,
  on_budget_cash: 1000,
  pre_budget_baseline: 0,
  held_in_envelopes: 500,
  held_outside_envelopes: 0,
  card_spend_not_covered: 0,
  assigned_in_future_months: 0,
  groups: [
    {
      id: 'g1',
      name: 'Everyday',
      collapsed: false,
      categories: [
        {
          key: 'env-dining',
          label: 'Dining',
          envelope_id: 'env-dining',
          category_ids: ['dining'],
          color: '#e0a032',
          assigned: 100,
          activity: -145.5,
          available: -45.5,
          target: null,
          needed_this_month: 0,
          status: 'overspent',
        },
      ],
      subtotal: { assigned: 100, activity: -145.5, available: -45.5 },
    },
  ],
  totals: { assigned: 100, activity: -145.5, available: -45.5, underfunded: 0 },
  nav: { current: '2026-07', prev: '2026-06', earliest: '2026-01', can_go_back: true },
  assigned_categories: ['dining'],
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date('2026-07-15T12:00:00Z'))
  clearQueryCache()
  mockAuthFetch.mockReset()
  mockAuthFetch.mockImplementation((url: string) => {
    if (url.startsWith('/api/planning/month')) return Promise.resolve(okJson(MONTH_DATA))
    if (url.startsWith('/api/categories')) return Promise.resolve(okJson([]))
    if (url.startsWith('/api/planning/notes')) return Promise.resolve(okJson({ notes: {} }))
    return Promise.resolve(okJson({}))
  })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

async function mountedModel() {
  const rendered = renderHook(() => useBudgetModel())
  await waitFor(() => expect(rendered.result.current.loading).toBe(false))
  return rendered
}

describe('Cover names its Ready-to-Assign cost', () => {
  it('leads the toast with what LEFT Ready to Assign, not with the reassuring half', async () => {
    const { result } = await mountedModel()
    const toast = vi.fn()
    await act(async () => {
      await result.current.moneyOp('env-dining', 'cover', undefined, toast)
    })
    expect(toast).toHaveBeenCalledWith('−S$45.50 from Ready to Assign — Dining is covered')
  })

  it('and the figure it names is real: Ready to Assign drops by the size of the hole', async () => {
    const { result } = await mountedModel()
    expect(result.current.rta).toBe(500)
    await act(async () => {
      await result.current.moneyOp('env-dining', 'cover', undefined, vi.fn())
    })
    expect(result.current.rta).toBe(454.5)
  })
})
