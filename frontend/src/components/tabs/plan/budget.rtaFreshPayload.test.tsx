// @vitest-environment jsdom
//
// QA + UI-design lanes, 2026-09-09: `[rtaLines] Ready to Assign does not match its own terms`
// STILL fired on every ordinary Budget load of a stock, unmutated book — it self-healed before
// paint, so nobody saw the "Unexplained" row, but the warning was real.
//
// The earlier fix moved the envelope term off the live model rows and onto the payload's own
// `held_in_envelopes`, which was correct — and left `rta` itself in a separate `useState`
// written by the SAME effect that syncs the model. An effect runs AFTER the render that
// triggered it, so on the render the payload lands, four of the identity's terms are already
// the new payload's and `rta` is still the previous one (0 on a first load). One render, one
// phantom residual, one warning. Same class of bug, one term further along.
//
// `rta` is now DERIVED from the payload (`ready_to_assign − heldOffset`, the offset our
// un-refetched writes accumulate and which the envelope term adds back), so all four terms
// move in one state update by construction and no render can see a mixed pair.
//
// This pin drives the REAL `useBudgetModel` — only `useBudget`, the fetch layer beneath it, is
// stubbed. Mocking `useBudgetModel` (as budget.rtaTerms.test.tsx must, for its own question)
// hands the component a hand-written, already-consistent `rta` and cannot see this at all.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'

vi.mock('../../../api/client', () => ({ authFetch: vi.fn(async () => ({ ok: false })) }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => vi.fn() }))
vi.mock('../../../hooks/useCommitments', () => ({
  useCommitments: () => ({ commitments: [], loading: false, refresh: vi.fn() }),
}))

const bud = vi.hoisted(() => ({ current: {} as Record<string, unknown> }))
vi.mock('../../../hooks/useBudget', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../hooks/useBudget')>()
  return { ...actual, useBudget: () => bud.current }
})

import { BudgetTab } from './budget'

function row(key: string, label: string, assigned: number, available: number) {
  return {
    key,
    label,
    envelope_id: key,
    category_ids: [`cat_${key}`],
    assigned,
    activity: 0,
    available,
    target: null,
    needed_this_month: 0,
    status: 'none',
  }
}

/** A self-consistent month payload: cash − baseline − held − card spending = Ready to Assign. */
function payload(month: string, cash: number, held: number, cardSpend: number) {
  const rta = Math.round((cash - held - cardSpend) * 100) / 100
  const cats = [row('env_rent', 'Rent', held + 50, held + 50), row('env_fun', 'Fun', 0, -50)]
  return {
    month,
    ready_to_assign: rta,
    on_budget_cash: cash,
    pre_budget_baseline: 0,
    rollover_start: null,
    has_foreign_cash: false,
    held_in_envelopes: held,
    card_spend_not_covered: cardSpend,
    assigned_in_future_months: 0,
    uncategorized_income_base: 0,
    groups: [
      {
        id: 'g-a',
        name: 'Everyday',
        collapsed: false,
        synced: false,
        categories: cats,
        subtotal: { assigned: held + 50, activity: 0, available: held },
      },
    ],
    totals: { assigned: held + 50, activity: 0, available: held, underfunded: 0 },
    nav: { can_go_back: true, prev: '2026-06' },
    assigned_categories: [],
  }
}

const noop = vi.fn(() => Promise.resolve([]))

function stubBudget(data: ReturnType<typeof payload>) {
  const base: Record<string, unknown> = {
    month: data.month,
    data,
    health: null,
    categories: [],
    hiddenKeys: new Set<string>(),
    notes: {},
    loading: false,
    error: '',
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

describe('the RTA identity closes on the very render its payload lands', () => {
  it('never warns on an ordinary first load of a healthy book', () => {
    bud.current = stubBudget(payload('2026-07', 5000, 900, 100)) as Record<string, unknown>
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(warn).not.toHaveBeenCalled()
  })

  it('never warns when a NEW month payload replaces the old one (month nav / refetch)', () => {
    bud.current = stubBudget(payload('2026-07', 5000, 900, 100)) as Record<string, unknown>
    const { rerender } = render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    bud.current = stubBudget(payload('2026-08', 8200, -340, 0)) as Record<string, unknown>
    rerender(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(warn).not.toHaveBeenCalled()
  })

  it('renders no "Unexplained" row once the popover is opened', async () => {
    const { fireEvent, screen } = await import('@testing-library/react')
    bud.current = stubBudget(payload('2026-07', 5000, 900, 100)) as Record<string, unknown>
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
    const panel = document.getElementById('pl-rtapop-panel')!
    expect(panel.textContent).not.toContain('Unexplained')
    expect(warn).not.toHaveBeenCalled()
  })
})
