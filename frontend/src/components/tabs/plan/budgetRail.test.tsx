// @vitest-environment jsdom
//
// feat/budget-summary-rail — the desktop-only (≥1024px) right-hand rail: two always-visible
// panels that otherwise only exist as click-to-open UI (the Ready-to-Assign breakdown popover,
// the Auto-assign dropdown). Coverage here is the rail's OWN pieces (BudgetSummaryPanel /
// BudgetAutoAssignPanel) plus BudgetTab's mount gate — the underlying math/item-list builders
// (buildRtaLines, buildAutoAssignItems) already have their own tests in budgetRta.test.ts /
// budgetParts.kebab.test.tsx and are not re-asserted here.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'

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

import { BudgetSummaryPanel, BudgetAutoAssignPanel } from './budgetRail'
import { BudgetTab } from './budget'

afterEach(() => {
  cleanup()
  localStorage.removeItem('pl-budget-rail-share')
})

const TERMS = {
  monthLabel: 'July 2026',
  totalAssigned: 500,
  totalActivity: -20,
  totalAvailable: 430,
  totalTargets: 120,
}

describe('BudgetSummaryPanel', () => {
  // Owner ask, 2026-09-15: dropped the RTA identity breakdown (cash/held/uncovered spend/
  // total) that used to render here too — real YNAB keeps that as its own popover (click the
  // RTA figure in the hero) and showing both here was redundant. This panel now mirrors real
  // YNAB's own "[Month]'s Summary" card: Left over / Assigned / Activity / Available, summed
  // across every envelope — the SAME figures as the table's own Total row.
  it('renders the month totals — left over, assigned, activity, then available', () => {
    render(<BudgetSummaryPanel {...TERMS} onBudgetCash={37665.52} />)
    expect(screen.getByText("July's Summary")).toBeTruthy()
    expect(screen.getByText('Left over from last month')).toBeTruthy()
    // Just "Assigned" — the panel title ("July's Summary") already names the month.
    expect(screen.getByText('Assigned')).toBeTruthy()
    expect(screen.getByText('Activity')).toBeTruthy()
    // Left over = available − assigned − activity = 430 − 500 − (−20) = −50.
    const rows = Array.from(document.querySelectorAll('.pl-rtapop-row, .pl-rtapop-total'))
    const leftOver = rows.find((r) => r.textContent?.startsWith('Left over from last month'))!
    expect(leftOver.textContent).toContain('50.00')
    const total = document.querySelector('.pl-rtapop-total--month')!
    expect(total.textContent).toContain('Available')
    expect(total.textContent).toContain('430.00')
  })

  // Age of money moved BACK into the hero (owner ask, 2026-09-14, reversing the earlier
  // same-day move into this panel) — see budget.tsx's Summary component instead.

  // Same guard RtaHero itself uses — a half-loaded month must look loading, not like a broken
  // (zeroed) breakdown.
  it('shows a loading state instead of a false breakdown while cash is not ready', () => {
    render(<BudgetSummaryPanel {...TERMS} onBudgetCash={null} />)
    expect(screen.getByText(/Loading/)).toBeTruthy()
    expect(screen.queryByText('Left over from last month')).toBeNull()
  })

  it('is collapsible, open by default', () => {
    render(<BudgetSummaryPanel {...TERMS} onBudgetCash={1000} />)
    const toggle = screen.getByRole('button', { expanded: true })
    expect(toggle).toBeTruthy()
    fireEvent.click(toggle)
    expect(screen.queryByText('Left over from last month')).toBeNull()
  })
})

const AUTOBUDGET = {
  underfundedCount: 2,
  underfundedAmount: 150,
  overspentCount: 1,
  overspentAmount: 40,
  totals: { assigned: 900, available: 0 },
}

describe('BudgetAutoAssignPanel', () => {
  // Labels/grouping match real YNAB's own Auto-Assign menu exactly (owner ask, 2026-09-15) —
  // "Underfunded" here is the fund-targets action, real YNAB's own name for it.
  it('renders every auto-assign mode and calls onPick with the clicked label', () => {
    const onPick = vi.fn()
    render(<BudgetAutoAssignPanel onPick={onPick} b={AUTOBUDGET} />)
    for (const label of [
      'Underfunded',
      'Assigned Last Month',
      'Spent Last Month',
      'Average Assigned',
      'Average Spent',
      'Reset Available Amounts',
      'Reset Assigned Amounts',
    ]) {
      expect(screen.getByText(label)).toBeTruthy()
    }
    fireEvent.click(screen.getByText('Underfunded'))
    expect(onPick).toHaveBeenCalledWith('Underfunded')
  })

  // Card-payment debt folds INTO the "Underfunded" hint (owner ask, 2026-09-15) — a separate
  // "Cards underfunded" row read as a confusing near-duplicate of the real "Underfunded"
  // action, and real YNAB shows exactly one "Underfunded" row with one number. The ACTION
  // itself still only funds envelope targets (buildClientAutoSteps already excludes payment
  // envelopes) — only the displayed total combines the two.
  it('folds card-payment debt into the single "Underfunded" hint, not a separate row', () => {
    render(
      <BudgetAutoAssignPanel onPick={vi.fn()} b={{ ...AUTOBUDGET, cardUnderfundedAmount: 88.4 }} />,
    )
    expect(screen.queryByText('Cards underfunded')).toBeNull()
    const row = screen.getByText('Underfunded').closest('.pl-budget-rail-pill')!
    // 150 (envelope underfunded) + 88.40 (card debt) = 238.40.
    expect(row.textContent).toContain('238.40')
  })

  it('shows just the envelope total when there is no card debt', () => {
    render(<BudgetAutoAssignPanel onPick={vi.fn()} b={AUTOBUDGET} />)
    const row = screen.getByText('Underfunded').closest('.pl-budget-rail-pill')!
    expect(row.textContent).toContain('150.00')
  })
})

/** Generic viewport-width matchMedia stub — handles BOTH useIsMobile's max-width query and
 *  useBreakpoint's ladder of min-width queries, since the rail's mount gate depends on both
 *  hooks agreeing. Mirrors budget.mobileCards.test.tsx's mockMobile, generalized to any width. */
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

function stubModel() {
  const noop = vi.fn(() => Promise.resolve([]))
  const base: Record<string, unknown> = {
    model: [],
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
      held_in_envelopes: 900,
      card_spend_not_covered: 100,
      assigned_in_future_months: 0,
      uncategorized_income_base: 0,
      groups: [],
      totals: { assigned: 0, activity: 0, available: 0, underfunded: 0 },
      nav: { can_go_back: true, prev: '2026-06' },
      assigned_categories: [],
    },
  }
  return new Proxy(base, { get: (t, k) => (k in t ? t[k as string] : noop) })
}

beforeEach(() => {
  model.current = stubModel() as Record<string, unknown>
})

describe('BudgetTab — the rail mounts on desktop only', () => {
  it('mounts .pl-rail and grids the layout at ≥1024px (lg)', () => {
    mockViewportWidth(1280)
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(document.querySelector('.pl-budget-rail')).toBeTruthy()
    expect(document.querySelector('.pl-budget-layout.has-rail')).toBeTruthy()
  })

  it('never mounts .pl-rail below 1024px (tablet portrait)', () => {
    mockViewportWidth(768)
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(document.querySelector('.pl-budget-rail')).toBeNull()
    expect(document.querySelector('.pl-budget-layout.has-rail')).toBeNull()
  })

  it('never mounts .pl-rail on a narrow (phone) viewport', () => {
    mockViewportWidth(375)
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    expect(document.querySelector('.pl-budget-rail')).toBeNull()
  })

  it('resizes and persists the rail through a touch pointer', () => {
    mockViewportWidth(1280)
    render(<BudgetTab toast={vi.fn()} onOpenTxn={vi.fn()} />)
    const rail = document.querySelector('.pl-budget-rail') as HTMLElement
    // The handle lives on the `.pl-budget-rail-col` wrapper, a SIBLING of the rail's own
    // scroll pane, not a descendant of it — a scroll pane would clip it (see
    // 6596cdfb4, "desktop Budget is a two-pane workspace").
    const handle = document.querySelector('.pl-budget-rail-handle') as HTMLElement
    const capture = vi.fn()
    Object.defineProperty(rail, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ width: 300 }),
    })
    Object.defineProperty(handle, 'setPointerCapture', { configurable: true, value: capture })

    fireEvent.pointerDown(handle, {
      pointerId: 9,
      pointerType: 'touch',
      isPrimary: true,
      clientX: 600,
    })
    fireEvent.pointerMove(handle, { pointerId: 9, clientX: 500 })
    fireEvent.pointerUp(handle, { pointerId: 9 })

    expect(capture).toHaveBeenCalledWith(9)
    expect(localStorage.getItem('pl-budget-rail-share')).toBe(String(400 / window.innerWidth))
  })
})
