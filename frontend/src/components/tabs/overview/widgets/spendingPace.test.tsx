// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const mockSummary = {
  days_in_month: 30,
  day_of_month: 12,
  pace_pct: 40,
  month: '2026-09',
  envelopes: {
    budgeted_rows: [
      {
        key: 'cat_groceries',
        label: 'Groceries',
        color: '#3b86dd',
        assigned: 800,
        spent: 420,
        fill_pct: 52.5,
        over: false,
        // Deliberately NOT what a naive client recomputation would give (420/12 = 35,
        // (800-420)/18 = 21.11, 800-420 = 380) — the regression this guards is the widget
        // doing the division/subtraction itself instead of rendering these verbatim.
        remaining: 999,
        avg_per_day: 111,
        left_per_day: 222,
      },
      {
        key: 'cat_dining',
        label: 'Food & Dining',
        color: '#e0a032',
        assigned: 300,
        spent: 120,
        fill_pct: 66,
        over: false,
        remaining: 999,
        avg_per_day: 111,
        left_per_day: 222,
      },
    ],
  },
}

vi.mock('../../../../hooks/serverState', () => ({
  usePlanningSummary: () => ({ data: mockSummary }),
  fetchJson: vi.fn().mockResolvedValue({ items: [] }),
}))

import { SpendingPaceWidget } from './spendingPace'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { fBase } from '../../../../utils/format'

afterEach(() => {
  cleanup()
})

describe('SpendingPaceWidget', () => {
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'spending-pace')!
  const instance: WidgetInstance = {
    instanceId: 'inst-spending-pace',
    defId: 'spending-pace',
    x: 0,
    y: 0,
    w: 6,
    h: 320,
    config: { categories: ['Groceries', 'Food & Dining'] },
  }

  it('renders pace rows for configured categories as pure display', () => {
    render(<SpendingPaceWidget instance={instance} def={def} onPatch={vi.fn()} />)
    expect(screen.getByText('Groceries')).toBeTruthy()
    expect(screen.getByText('Food & Dining')).toBeTruthy()
    // Verify progress bars are present
    expect(screen.getByLabelText('Groceries: 53 percent of budget spent')).toBeTruthy()
    expect(screen.getByLabelText('Food & Dining: 66 percent of budget spent')).toBeTruthy()
    // Verify rows are static (not buttons or interactive drawers)
    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.queryByText(/Projected spend/)).toBeNull()
  })

  it('renders the server-computed pace fields verbatim, never recomputed client-side', () => {
    // Regression for the ADR 2026-07-31 violation: the widget used to derive avg/day,
    // left/day and "remaining" itself from `assigned`/`spent`/`day_of_month`. The mock's
    // `remaining`/`avg_per_day`/`left_per_day` are deliberately NOT what that naive
    // recomputation would give (300 assigned, 120 spent, day 12 of 30 -> naive remaining
    // 180, naive avg/day 10, naive left/day 10) — asserting the SERVER'S figures (999/111/222)
    // is what would actually catch the widget doing the math again.
    const { container } = render(
      <SpendingPaceWidget instance={instance} def={def} onPatch={vi.fn()} />,
    )
    const diningRow = Array.from(container.querySelectorAll('.row')).find((el) =>
      el.textContent?.includes('Food & Dining'),
    )
    const stats = diningRow?.querySelector('.stats')
    expect(stats?.textContent).toContain(fBase(999)) // server "remaining"
    expect(stats?.textContent).toContain(fBase(111)) // server "avg_per_day"
    expect(stats?.textContent).toContain(fBase(222)) // server "left_per_day"

    // A naive client recomputation would show these instead — must NOT appear.
    expect(stats?.textContent).not.toContain(fBase(180)) // 300 - 120
    expect(stats?.textContent).not.toContain(fBase(10)) // 120/12 and 180/18 both naively equal 10
  })

  it('with no categories picked, shows the top budgeted spenders', () => {
    const { container } = render(
      <SpendingPaceWidget
        instance={{ ...instance, config: { categories: [] } }}
        def={def}
        onPatch={vi.fn()}
      />,
    )
    const labels = Array.from(container.querySelectorAll('.row .category')).map((el) =>
      el.textContent?.trim(),
    )
    expect(labels).toEqual(['Groceries', 'Food & Dining'])
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('says so when no picked category is a budgeted envelope, instead of silently substituting', () => {
    render(
      <SpendingPaceWidget
        instance={{ ...instance, config: { categories: ['Shopping', 'Travel'] } }}
        def={def}
        onPatch={vi.fn()}
      />,
    )
    const note = screen.getByRole('note')
    expect(note.textContent).toContain('Shopping, Travel')
    expect(screen.getByText('Groceries')).toBeTruthy()
  })

  it('the default setting is "top spending", not a fixed list of category names', () => {
    const setting = def.settings?.find((s) => s.key === 'categories')
    expect(setting?.default).toEqual([])
  })
})
