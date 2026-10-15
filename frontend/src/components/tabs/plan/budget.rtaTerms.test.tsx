// @vitest-environment jsdom
//
// QA, 2026-09-09: `[rtaLines] Ready to Assign does not match its own terms` fired on EVERY
// ordinary Budget load of a perfectly healthy book. Four of the identity's terms (cash, card
// spending, baseline, assigned-in-future) are read straight off `m.data`, but the envelope term
// was re-summed from the LIVE model rows — which lag the payload by a render on any month change
// or refetch, and can't see a retired payment envelope at all. Two states, one subtraction, a
// phantom residual. It is now sourced from ONE place like the rest: the payload's own
// `held_in_envelopes` plus `m.heldOffset`, the offset our un-refetched writes accumulate.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup, fireEvent, screen } from '@testing-library/react'

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
import { statusOf } from './planData'

function mkCat(label: string, assigned: number, available: number) {
  return {
    key: `env-${label}`,
    envelopeId: `env-${label}`,
    categoryIds: [`cat_${label.toLowerCase().padEnd(12, '0')}`],
    label,
    color: '#34b08a',
    assigned,
    activity: 0,
    available,
    target: 0,
    tt: null,
    cad: null,
    ...statusOf(assigned, 0, available, 0, null),
  }
}

/** A book mid-refetch: the payload is self-consistent (5000 − 900 − 100 = 4000) while the model
 *  still holds the PREVIOUS month's rows, which sum to a held figure of 500. Summing the term
 *  off those rows produced a phantom −400 residual; reading `held_in_envelopes` cannot. */
function stubModel(heldBase: number, rowsHeld: number) {
  const noop = vi.fn(() => Promise.resolve([]))
  const cats = [mkCat('Rent', rowsHeld, rowsHeld), mkCat('Groceries', 0, -50)]
  const base: Record<string, unknown> = {
    model: [
      {
        id: 'g-a',
        name: 'Everyday',
        collapsed: false,
        hidden: false,
        synced: false,
        subtotal: { assigned: rowsHeld, activity: 0, available: rowsHeld - 50 },
        cats,
      },
    ],
    rta: 4000,
    categories: [],
    month: '2026-07',
    monthLabel: 'July 2026',
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
      month: '2026-07',
      ready_to_assign: 4000,
      on_budget_cash: 5000,
      pre_budget_baseline: 0,
      has_foreign_cash: false,
      held_in_envelopes: heldBase,
      card_spend_not_covered: 100,
      assigned_in_future_months: 0,
      uncategorized_income_base: 0,
      groups: [],
      totals: { assigned: rowsHeld, activity: 0, available: rowsHeld - 50, underfunded: 0 },
      nav: { can_go_back: true, prev: '2026-06' },
      assigned_categories: [],
    },
  }
  return new Proxy(base, { get: (t, k) => (k in t ? t[k as string] : noop) })
}

let warn: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe("the RTA breakdown's terms all come from ONE payload", () => {
  it('never warns on load, even while the model rows lag the payload', () => {
    model.current = stubModel(900, 500) as Record<string, unknown>
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(warn).not.toHaveBeenCalled()
  })

  it('renders the payload envelope term, and no "Unexplained" row, when opened', () => {
    model.current = stubModel(900, 500) as Record<string, unknown>
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
    const panel = document.getElementById('pl-rtapop-panel')!
    const rows = [...panel.querySelectorAll('.pl-rtapop-row')].map((r) => [
      r.querySelector('.lbl')!.textContent,
      r.querySelector('.val')!.textContent,
    ])
    expect(rows).toEqual([
      ['Cash on hand', 'S$5,000.00'],
      ['Held in envelopes', 'S$900.00'],
      ['Card spending not covered this month', 'S$100.00'],
    ])
    expect(panel.textContent).not.toContain('Unexplained')
    expect(warn).not.toHaveBeenCalled()
  })

  it('stays silent on a book where the rows happen to agree with the payload', () => {
    model.current = stubModel(900, 900) as Record<string, unknown>
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(warn).not.toHaveBeenCalled()
  })
})
