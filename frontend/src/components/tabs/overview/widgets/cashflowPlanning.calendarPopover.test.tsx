// @vitest-environment jsdom
//
// The spending calendar's day popover (owner, 2026-09-20): it is a body portal, so the widget's
// scroll box can never clip its header; "Open in register" is offered on HOVER, not only after a
// tap; and moving focus/pointer into the popover keeps it open long enough to click that link.

import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const summary = {
  month: '2026-09',
  calendar: {
    month: '2026-09',
    days_in_month: 30,
    first_weekday: 2,
    days: [
      { day: 17, in: 120, out: 303.2, net: -183.2 },
      { day: 18, in: 0, out: 0, net: 0 },
    ],
    total_in: 120,
    total_out: 303.2,
    max_out: 303.2,
    max_flow: 303.2,
  },
}
vi.mock('../../../../hooks/serverState', () => ({
  usePlanningSummary: () => ({ data: summary, initialLoading: false, error: null }),
  useCashflowProjections: () => ({ data: null, initialLoading: false, error: null }),
}))
vi.mock('../useWidgetData', () => ({
  useAnalytics: () => ({ analytics: null, loading: false, error: false, netWorth: 0 }),
}))
vi.mock('../../../../hooks/useFire', () => ({
  useFire: () => ({ data: null, loading: false, error: false, retry: vi.fn() }),
}))

import { cashflowPlanningWidgetComponents } from './cashflowPlanning'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'

// The fixture's month is September 2026; the widget opens on the CURRENT month, so pin "today".
beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(new Date(2026, 8, 20))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

function renderCalendar(onOpenRegister = vi.fn()) {
  const Comp = cashflowPlanningWidgetComponents['calendar']!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'calendar')!
  const instance: WidgetInstance = {
    instanceId: 'w-calendar',
    defId: 'calendar',
    x: 0,
    y: 0,
    w: 6,
    h: 260,
    config: {},
  }
  const view = render(
    <Comp instance={instance} def={def} onPatch={vi.fn()} onOpenRegister={onOpenRegister} />,
  )
  return { ...view, onOpenRegister }
}

function dayButton(day: number): HTMLButtonElement {
  return screen.getByText(String(day)).closest('button') as HTMLButtonElement
}

function popover(): HTMLElement | null {
  return document.body.querySelector('.ov-cal-pop')
}

describe('Spending calendar — day popover', () => {
  it('renders as a body portal and offers "Open in register" on hover', () => {
    const { container, onOpenRegister } = renderCalendar()
    fireEvent.mouseEnter(dayButton(17))

    const pop = popover()
    expect(pop).not.toBeNull()
    // Outside the widget's own (clipping) box: a direct child of <body>, not of the grid.
    expect(container.contains(pop)).toBe(false)
    expect(pop!.parentElement).toBe(document.body)
    expect(pop!.style.position || getComputedStyle(pop!).position).not.toBe('absolute')

    const link = screen.getByText('Open in register')
    fireEvent.click(link)
    expect(onOpenRegister).toHaveBeenCalledWith({ dateOn: '2026-09-17' })
    expect(popover()).toBeNull()
  })

  it('survives the day losing focus to the popover, and a pointer crossing into it', () => {
    vi.useFakeTimers()
    renderCalendar()
    const day = dayButton(17)
    fireEvent.mouseEnter(day)
    const pop = popover()!
    const link = screen.getByText('Open in register')

    // Clicking the link first blurs the day — that blur must not dismiss the popover.
    fireEvent.blur(day, { relatedTarget: link })
    expect(popover()).toBe(pop)

    // Leaving the grid for the popover: the dismissal is deferred, and entering cancels it.
    fireEvent.mouseLeave(day.closest('.ov-cal-wrap')!)
    fireEvent.mouseEnter(pop)
    act(() => {
      vi.advanceTimersByTime(300)
    })
    expect(popover()).toBe(pop)

    // Leaving the popover itself dismisses it.
    fireEvent.mouseLeave(pop)
    expect(popover()).toBeNull()
  })

  it('shows nothing for a day with no activity, and dismisses on scroll', () => {
    renderCalendar()
    fireEvent.mouseEnter(dayButton(18))
    expect(popover()).toBeNull()

    fireEvent.mouseEnter(dayButton(17))
    expect(popover()).not.toBeNull()
    fireEvent.scroll(window)
    expect(popover()).toBeNull()
  })
})
