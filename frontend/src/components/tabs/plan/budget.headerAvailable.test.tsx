// @vitest-environment jsdom
//
// The Budget summary's "available" is the PLAIN envelope sum — every envelope's Available,
// negatives included, and nothing else (ADR 2026-09-08 supersedes the display-total fold of
// `2026-09-07-budget-available-is-the-display-total`). Spending that belongs to no envelope is
// the outside-spending banner's job; folding it into a number labelled "available" made the
// header answer a question nobody asked, and needed a tooltip to say so.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, cleanup } from '@testing-library/react'

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
import { fBase, fSmart, statusOf } from './planData'

// Synthetic book: one envelope carrying last month's hole (nothing assigned, nothing spent this
// month), one overspent this month, one funded. S$5.00 of spending has no category (Uncategorized)
// and S$207.68 sits in a labeled category with no envelope (Unbudgeted) — neither belongs to
// the header's number.
const CARRIED_HOLE = -50
const ENVELOPE_PURE = 430 // 500 funded − 50 carried − 20 overspent
const UNSORTED = -5
const UNBUDGETED = -207.68
const OLD_DISPLAY_TOTAL = 217.32 // what the retired fold would have shown

function mkCat(label: string, assigned: number, activity: number, available: number) {
  return {
    key: `env-${label}`,
    envelopeId: `env-${label}`,
    categoryIds: [`cat_${label.toLowerCase().padEnd(12, '0')}`],
    label,
    color: '#34b08a',
    assigned,
    activity,
    available,
    target: 0,
    tt: null,
    cad: null,
    ...statusOf(assigned, activity, available, 0, null),
  }
}

function stubModel() {
  const noop = vi.fn(() => Promise.resolve([]))
  const cats = [
    mkCat('Groceries', 0, 0, CARRIED_HOLE),
    mkCat('Transport', 0, -20, -20),
    mkCat('Rent', 500, 0, 500),
  ]
  const base: Record<string, unknown> = {
    model: [
      {
        id: 'g-a',
        name: 'Everyday',
        collapsed: false,
        hidden: false,
        synced: false,
        subtotal: { assigned: 500, activity: -20, available: ENVELOPE_PURE },
        cats,
      },
    ],
    rta: 4500,
    categories: [{ id: 'cat_gifts000000', label: 'Gifts', color: '#c98a3a' }],
    month: '2026-07',
    monthLabel: 'July 2026',
    loading: false,
    health: null,
    notes: {},
    hiddenKeys: new Set<string>(),
    undo: null,
    heldOffset: 0,
    unsortedActivity: UNSORTED,
    unbudgetedActivity: UNBUDGETED,
    unbudgetedGroup: {
      id: '__unbudgeted__',
      name: 'Unbudgeted',
      categories: [{ key: 'cat_gifts000000', label: 'Gifts', activity: UNBUDGETED }],
    },
    data: {
      month: '2026-07',
      ready_to_assign: 4500,
      on_budget_cash: 5000,
      pre_budget_baseline: 0,
      has_foreign_cash: false,
      held_in_envelopes: 500,
      held_outside_envelopes: 0,
      card_spend_not_covered: 0,
      assigned_in_future_months: 0,
      uncategorized_income_base: 0,
      groups: [],
      totals: { assigned: 500, activity: -20, available: ENVELOPE_PURE, underfunded: 0 },
      nav: { can_go_back: true, prev: '2026-06' },
      assigned_categories: [],
    },
  }
  return new Proxy(base, { get: (t, k) => (k in t ? t[k as string] : noop) })
}

beforeEach(() => {
  model.current = stubModel() as Record<string, unknown>
})
afterEach(() => cleanup())

describe('the Budget summary "available" is the plain envelope sum', () => {
  it('the mini "available" line and the Total row both show it, untooltipped', () => {
    // The header's own "Total available" stat block is gone (owner ask, 2026-09-14 — it
    // duplicated the filter chips/rail, matching YNAB's own minimal header); this figure now
    // surfaces only via the mini line and the table's Total row, both checked below.
    const { container } = render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    const shown = fBase(ENVELOPE_PURE)
    expect(shown).not.toBe(fBase(OLD_DISPLAY_TOTAL))

    const mini = Array.from(container.querySelectorAll('.pl-sum-mini-stat')).find((el) =>
      el.textContent?.endsWith(' available'),
    )!
    expect(mini.textContent).toBe(`${shown} available`)
    expect(mini.getAttribute('title')).toBeNull()

    // The Total row's Available cell renders through AvailCell (the SAME tappable pill every
    // envelope row's Available column uses), which is deliberately "smart" precision — cents
    // shown only when the amount actually has them — so it matches the column it sits in, not
    // the Summary stat's always-2-decimal fBase. Both show the identical VALUE; only whole vs.
    // fractional values can legitimately differ in STRING form between the two formatters.
    const totalRow = container.querySelector('.pl-trow--total')!
    expect(totalRow.textContent).toContain(fSmart(ENVELOPE_PURE))
  })

  // Owner ask, 2026-09-15: the coaching ribbon is parked behind BudgetInsight's `HIDDEN`
  // flag, so the whole `.pl-sum-action` band drops out — even in this fixture's overspent
  // state, which used to render the "2 envelopes are overspent…" line here. The branch copy
  // itself still has its own (skipped) coverage in budget.insight.test.tsx.
  it('renders no coaching band while the insight ribbon is parked', () => {
    const { container } = render(<BudgetTab toast={vi.fn()} />)
    expect(container.querySelector('.pl-sum-insight')).toBeNull()
    expect(container.querySelector('.pl-sum-action')).toBeNull()
  })

  it('reads a carried negative from last month as overspent, for the amount carried', () => {
    const { container } = render(<BudgetTab toast={vi.fn()} />)
    const row = Array.from(container.querySelectorAll('.pl-trow')).find((el) =>
      el.textContent?.startsWith('Groceries'),
    )!
    // The amount is deliberately NOT in this line anymore (owner ask, 2026-09-15) — it's the
    // row's own Available cell. The STATUS is what's asserted here.
    expect(row.querySelector('.pl-cstatus.over')!.textContent).toBe('Overspent')
  })

  // Owner, 2026-09-25: no warning banner above the table. Spending outside every envelope is
  // named where it lives — the table's Outside envelopes rows, each with its own action.
  it('names spending outside every envelope in the table, not in a banner', () => {
    const { container } = render(<BudgetTab toast={vi.fn()} />)
    expect(container.querySelector('.pl-uncat')).toBeNull()
    const gifts = Array.from(container.querySelectorAll('.pl-trow')).find((el) =>
      el.textContent?.startsWith('Gifts'),
    )!
    expect(gifts.querySelector('.pl-caction.addenv')!.textContent).toBe('Add to envelope…')
  })

  // Desktop keeps its original layout — nav / hero-block (eyebrow + figure) / Age of money,
  // all in one row — unchanged. The "eyebrow+nav row, full-width figure, no Age of money"
  // shape is MOBILE ONLY (owner-caught regression, 2026-09-15: it briefly landed in the
  // unconditional markup and overwrote desktop too); see budget.mobileCards-style coverage
  // for the mobile branch, driven by `isMobile`, not a @container query.
  it('desktop still shows Age of money alongside the nav and the hero-block', () => {
    const { container } = render(<BudgetTab toast={vi.fn()} />)
    const row = container.querySelector('.pl-sum-row')!
    expect(row.textContent).toContain('Age of money')
    expect(row.querySelector('.pl-sum-hero-block')).toBeTruthy()
    expect(row.querySelector('.pl-mnav')).toBeTruthy()
    expect(row.querySelector('.pl-sum-headrow')).toBeNull()
  })
})
