// @vitest-environment jsdom
//
// feat/budget-summary-rail follow-up, issue #3 — on desktop (showRail) opening a category's
// target editor swaps `.pl-budget-rail` to the SAME TargetEditor form (inline, replacing
// Summary/Auto-assign) instead of floating a PlanningModal on top of them; canceling restores
// the two panels. Below the rail's breakpoint the floating-modal behavior must stay byte-for-
// byte what it was before this feature — proven here by asserting `.pl-tgt-modal` still opens
// and `.pl-budget-rail` never exists at all at that width.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, cleanup, fireEvent, act } from '@testing-library/react'

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

function mkCat(label: string, target: number) {
  return {
    key: `env-${label}`,
    envelopeId: `env-${label}`,
    categoryIds: [`cat_${label.toLowerCase().padEnd(12, '0')}`],
    label,
    color: '#34b08a',
    assigned: target,
    activity: 0,
    available: target,
    target,
    tt: 'set_aside',
    cad: 'monthly',
    ...statusOf(target, 0, target, 0, null),
  }
}

function stubModel(cats = [mkCat('Groceries', 500)]) {
  const noop = vi.fn(() => Promise.resolve([]))
  const base: Record<string, unknown> = {
    model: [
      {
        id: 'g-a',
        name: 'Everyday',
        collapsed: false,
        hidden: false,
        synced: false,
        subtotal: { assigned: 500, activity: 0, available: 500 },
        cats,
      },
    ],
    rta: 4500,
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
      totals: { assigned: 500, activity: 0, available: 500, underfunded: 0 },
      nav: { can_go_back: true, prev: '2026-06' },
      assigned_categories: [],
    },
  }
  return new Proxy(base, { get: (t, k) => (k in t ? t[k as string] : noop) })
}

/** Same generic viewport-width matchMedia stub as budgetRail.test.tsx, so the rail's mount
 *  gate (useIsMobile + useBreakpoint) and the label-click's own debounce timer agree. */
function mockViewportWidth(px: number) {
  const fn = ((query: string) => {
    const min = query.match(/min-width:\s*([\d.]+)px/)
    const max = query.match(/max-width:\s*([\d.]+)px/)
    let matches = true
    if (min) matches = matches && px >= parseFloat(min[1]!)
    if (max) matches = matches && px <= parseFloat(max[1]!)
    return {
      matches,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }
  }) as unknown as typeof window.matchMedia
  Object.defineProperty(window, 'matchMedia', { value: fn, configurable: true, writable: true })
}

// The category label's click handler debounces 220ms (to disambiguate from a rename
// double-click) before calling onEditTarget — fake timers make that deterministic instead of
// racing a real 220ms wait.
beforeEach(() => {
  model.current = stubModel() as Record<string, unknown>
  vi.useFakeTimers({ shouldAdvanceTime: true })
})
afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function clickCategoryLabel(container: HTMLElement, name = 'Groceries') {
  const label = Array.from(container.querySelectorAll('.pl-clabel')).find(
    (el) => el.textContent === name,
  ) as HTMLElement
  fireEvent.click(label)
  act(() => {
    vi.advanceTimersByTime(300)
  })
}

describe('desktop (showRail): the target editor renders INSIDE the rail, not a floating modal', () => {
  it('swaps .pl-budget-rail to the target form, replacing Summary/Auto-assign; Back restores them', () => {
    mockViewportWidth(1280)
    const { container } = render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)

    // BudgetSummaryPanel's own RTA total row + BudgetAutoAssignPanel's own pill list —
    // markers unique to each panel (plain text like "Auto-assign" is ambiguous: the target
    // form has its OWN "Auto-assign" collapsible section). Since 2026-09-15 that section
    // renders through the SAME `.pl-budget-rail-pills` treatment (one visual language for one
    // set of actions), so the marker has to exclude the target form's own list by name.
    const rail = () => container.querySelector('.pl-budget-rail') as HTMLElement
    expect(rail()).toBeTruthy()
    expect(rail().querySelector('.pl-rtapop-total')).toBeTruthy()
    expect(rail().querySelector('.pl-budget-rail-pills:not(.pl-tgt-auto-list)')).toBeTruthy()
    expect(rail().querySelector('.pl-budget-rail-target')).toBeNull()

    clickCategoryLabel(container)

    // The form is now INSIDE the rail — no floating PlanningModal was mounted at all.
    expect(rail().querySelector('.pl-budget-rail-target')).toBeTruthy()
    expect(rail().textContent).toContain('Groceries')
    expect(rail().textContent).toContain('Target')
    expect(rail().querySelector('.pl-rtapop-total')).toBeNull()
    expect(rail().querySelector('.pl-budget-rail-pills:not(.pl-tgt-auto-list)')).toBeNull()
    expect(document.querySelector('.pl-tgt-modal')).toBeNull()

    fireEvent.click(rail().querySelector('.pl-budget-rail-target button') as HTMLElement)

    expect(rail().querySelector('.pl-budget-rail-target')).toBeNull()
    expect(rail().querySelector('.pl-rtapop-total')).toBeTruthy()
    expect(rail().querySelector('.pl-budget-rail-pills:not(.pl-tgt-auto-list)')).toBeTruthy()
  })
})

describe('narrow viewport (no rail): the floating modal is completely unchanged', () => {
  it('opens .pl-tgt-modal as before; .pl-budget-rail never exists', () => {
    mockViewportWidth(768)
    const { container } = render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)

    expect(container.querySelector('.pl-budget-rail')).toBeNull()

    clickCategoryLabel(container)

    expect(document.querySelector('.pl-tgt-modal')).toBeTruthy()
    expect(document.querySelector('.pl-budget-rail')).toBeNull()
  })
})

// Owner, 2026-09-25: the rail's editor stayed MOUNTED when another envelope's target was clicked
// without pressing Back, so its form state (seeded from the envelope once, on mount) carried
// the previous envelope's unsaved draft into the next one — and Save would write it there.
describe('switching envelopes while the rail editor is open', () => {
  it("starts the next envelope's form fresh instead of carrying the previous draft", () => {
    mockViewportWidth(1280)
    model.current = stubModel([mkCat('Groceries', 500), mkCat('Utilities', 0)]) as Record<
      string,
      unknown
    >
    const { container } = render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    const rail = () => container.querySelector('.pl-budget-rail') as HTMLElement

    // Utilities has no target: its Target card offers "Create target"; open the form and type
    // an unsaved S$300.
    clickCategoryLabel(container, 'Utilities')
    fireEvent.click(
      Array.from(rail().querySelectorAll('button')).find(
        (b) => b.textContent === 'Create target',
      ) as HTMLElement,
    )
    const amount = rail().querySelector('.pl-tgt-amt input') as HTMLInputElement
    fireEvent.change(amount, { target: { value: '300' } })
    expect(amount.value).toBe('300')

    // Open Groceries without pressing Back: it must show ITS OWN saved target, not the draft.
    clickCategoryLabel(container, 'Groceries')
    expect(rail().textContent).toContain('Groceries')
    const carried = rail().querySelector('.pl-tgt-amt input') as HTMLInputElement | null
    expect(carried?.value).not.toBe('300')
  })
})

// Owner, 2026-09-25 (YNAB's payment inspector): on desktop a card's payment row opens its
// payment detail in the side panel instead of jumping to Accounts.
describe('desktop: a card payment row opens its payment detail in the side panel', () => {
  it('shows Payment / This month cards; Back restores the month panels', () => {
    mockViewportWidth(1280)
    const payment = {
      ...mkCat('Citi Rewards', 0),
      key: 'env-cc',
      envelopeId: 'env-cc',
      categoryIds: [],
      isPaymentEnvelope: true,
      paymentOwed: 5351.52,
      availableForPayment: 5.5,
      paymentUnderfunded: 0,
    }
    const base = stubModel() as Record<string, unknown>
    const groups = base.model as Array<Record<string, unknown>>
    base.model = [
      {
        id: 'g-cc',
        name: 'Credit Card Payments',
        collapsed: false,
        hidden: false,
        synced: true,
        subtotal: { assigned: 0, activity: 0, available: 5.5 },
        cats: [payment],
      },
      ...groups,
    ]
    model.current = base
    const { container } = render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    const rail = () => container.querySelector('.pl-budget-rail') as HTMLElement

    const row = Array.from(container.querySelectorAll('.pl-crow')).find((r) =>
      r.textContent?.includes('Citi Rewards'),
    ) as HTMLElement
    fireEvent.click(row)

    expect(rail().textContent).toContain('Citi Rewards')
    expect(rail().textContent).toContain('Payment')
    expect(rail().textContent).toContain('Current balance')
    expect(rail().querySelector('.pl-rtapop-total')).toBeNull()

    fireEvent.click(rail().querySelector('.pl-budget-rail-target button') as HTMLElement)
    expect(rail().querySelector('.pl-rtapop-total')).toBeTruthy()
  })
})
