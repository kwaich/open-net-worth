// @vitest-environment jsdom
//
// "Fresh start · since <month>" — the one explainer allowed back under the clean RTA hero.
// With a carry floor set, every envelope's carry-in is deliberately 0 and RTA is computed from
// that month's cash frame; without this line nothing on the screen says so, and a reader has no
// way to tell a fresh start from a book that genuinely carried nothing. Hidden when null.
// Follows budget.headerAvailable.test.tsx's stubbed-useBudgetModel convention.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../../hooks/useCommitments', () => ({
  useCommitments: () => ({ commitments: [], loading: false, refresh: vi.fn() }),
}))

const model = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))
vi.mock('./useBudgetModel', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./useBudgetModel')>()
  return { ...actual, useBudgetModel: () => model.current }
})

import { BudgetTab } from './budget'

function stubModel(rolloverStart: string | null) {
  const noop = vi.fn(() => Promise.resolve([]))
  const base: Record<string, unknown> = {
    model: [],
    rta: 1234,
    categories: [],
    month: '2026-09',
    monthLabel: 'September 2026',
    loading: false,
    health: null,
    notes: {},
    hiddenKeys: new Set<string>(),
    undo: null,
    heldOffset: 0,
    unsortedActivity: 0,
    unbudgetedActivity: 0,
    unbudgetedGroup: null,
    data: {
      month: '2026-09',
      ready_to_assign: 1234,
      on_budget_cash: 1234,
      pre_budget_baseline: 0,
      rollover_start: rolloverStart,
      has_foreign_cash: false,
      held_in_envelopes: 0,
      card_spend_not_covered: 0,
      assigned_in_future_months: 0,
      uncategorized_income_base: 0,
      groups: [],
      totals: { assigned: 0, activity: 0, available: 0, underfunded: 0 },
      nav: { can_go_back: true, prev: '2026-08' },
      assigned_categories: [],
    },
  }
  return new Proxy(base, { get: (t, k) => (k in t ? t[k as string] : noop) })
}

beforeEach(() => cleanup())
afterEach(() => cleanup())

function renderWith(rolloverStart: string | null) {
  model.current = stubModel(rolloverStart) as Record<string, unknown>
  return render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} onOpenRegister={vi.fn()} />)
}

// Owner, 2026-09-25: the hero no longer carries a "Fresh start · since …" line — the ⋯ menu
// already reads "Started fresh from <month>".
describe('the RTA hero after a fresh start', () => {
  it('shows no fresh-start line, whatever the carry floor', () => {
    renderWith('2026-09')
    expect(screen.queryByText(/Fresh start/)).toBeNull()
    cleanup()
    renderWith(null)
    expect(screen.queryByText(/Fresh start/)).toBeNull()
  })
})
