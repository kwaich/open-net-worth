// cashflowPlanning.planFire.test.tsx — MATH-28 regression for the Overview `plan-fire`
// widget: a missing date_of_birth must degrade gracefully (a one-line hint in place of the
// age-based chart) instead of plotting an "age 0" trajectory or a fabricated on_track verdict.
// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../useWidgetData', () => ({
  useAnalytics: () => ({ analytics: null, loading: false, error: false, netWorth: 500_000 }),
}))

const fireData = vi.hoisted(() => ({ current: null as unknown }))
vi.mock('../../../../hooks/useFire', () => ({
  useFire: () => ({ data: fireData.current, loading: false, error: false, retry: vi.fn() }),
}))

vi.mock('../charts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../charts')>()
  return {
    ...actual,
    OvChart: () => <div data-testid="ov-chart" />,
  }
})

import { cashflowPlanningWidgetComponents } from './cashflowPlanning'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

afterEach(cleanup)

function instanceOf(defId: string): WidgetInstance {
  return { instanceId: `w-${defId}`, defId, x: 0, y: 0, w: 6, h: 260, config: {} }
}

function renderPlanFire() {
  const Comp = cashflowPlanningWidgetComponents['plan-fire']!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'plan-fire')!
  return render(
    <Comp instance={instanceOf('plan-fire')} def={def} onPatch={vi.fn()} onNavigate={vi.fn()} />,
  )
}

const trajectory = (age: number | null) => [
  { year: 2026, age, nw: 500_000, target: 1_800_000 },
  { year: 2027, age: age == null ? null : age + 1, nw: 550_000, target: 1_800_000 },
]

const baseFireResult = {
  fire_number: 1_800_000,
  current_nw: 500_000,
  progress_pct: 27.8,
  monthly_retirement_spending: 6_000,
  monthly_savings: 4_200,
  expected_return_pct: 6.5,
  withdrawal_rate_pct: 4,
  retirement_target_age: 55,
  years_to_fire: 10,
  monthly_savings_needed: 0,
  retirement_duration_years: 35,
  recommended_swr_pct: 3.75,
  swr_warning: false,
  volatility_pct: 14,
  life_expectancy: 90,
}

describe('PlanFire widget — missing date of birth (MATH-28)', () => {
  it('shows the date-of-birth hint instead of the chart, and no "age 0" text', () => {
    fireData.current = {
      ...baseFireResult,
      current_age: null,
      projected_fire_age: null,
      on_track: null,
      trajectory: trajectory(null),
    }
    renderPlanFire()
    expect(screen.getByText('Add your date of birth in Profile to see ages')).toBeTruthy()
    expect(screen.queryByTestId('ov-chart')).toBeNull()
    expect(screen.queryByText('age 0')).toBeNull()
    expect(screen.queryByText(/\bAge 0\b/)).toBeNull()
    // Years-based figures stay visible.
    expect(screen.getByText('FIRE number')).toBeTruthy()
    expect(screen.getByText('10.0 yrs')).toBeTruthy()
    expect(screen.getByText((_, el) => el?.textContent === '• FI —')).toBeTruthy()
  })

  it('renders the chart (no hint) when a real date of birth is on file', () => {
    fireData.current = {
      ...baseFireResult,
      current_age: 38,
      projected_fire_age: 48,
      on_track: true,
      trajectory: trajectory(38),
    }
    renderPlanFire()
    expect(screen.getByTestId('ov-chart')).toBeTruthy()
    expect(screen.queryByText('Add your date of birth in Profile to see ages')).toBeNull()
    expect(screen.getByText((_, el) => el?.textContent === '• FI ~48')).toBeTruthy()
  })
})
