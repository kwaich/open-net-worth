// @vitest-environment jsdom
//
// The RTA breakdown popover's flow-framed rendering branch (design doc 2026-09-13, trimmed and
// refined 2026-09-14 per owner-supplied real YNAB screenshots): when the server sends a real
// `flow` object, RtaHero switches from the stock Cash-on-hand ledger to YNAB's own flow
// framing — no text section headers, no per-row explanatory notes and no forward-looking
// footnote. The +/− glyph belongs to a dedicated section-header row (glyph + that section's
// own subtotal, no label) — every item row underneath carries its own sign on the amount
// instead, never a repeated glyph. Month names in row labels drop the year. A coaching line
// and a permanent info banner sit below the total, matching YNAB's own copy. `budgetRta.test.ts`
// proves the flow==null stock path in full (unchanged) — this file proves the flow branch.
import { describe, it, expect, afterEach, vi } from 'vitest'
import { createElement } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { RtaHero } from './budgetRta'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const BASE_PROPS = {
  monthLabel: 'August 2026',
  prevMonthLabel: 'July 2026',
  heldInEnvelopes: 0,
  heldOutsideEnvelopes: 0,
  cardSpendNotCovered: 0,
  baseline: 0,
  assignedInFuture: 0,
  underfundedCount: 0,
  onFundUnderfunded: () => {},
}

function openPanel(props: Record<string, unknown>) {
  render(createElement(RtaHero, props as never))
  fireEvent.click(screen.getByRole('button', { name: /Ready to Assign/ }))
  return document.getElementById('pl-rtapop-panel')!
}

describe('RtaHero — flow==null falls back to the stock ledger', () => {
  it('shows the stock rows with no section headers and no first-month subline', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const panel = openPanel({
      ...BASE_PROPS,
      flow: null,
      onBudgetCash: 1000,
      rta: 1000,
    })
    expect(panel.textContent).not.toContain('First month of your plan')
    expect(panel.textContent).toContain('Cash on hand')
    expect(panel.querySelectorAll('.pl-rtapop-sec')).toHaveLength(0)
    expect(warn).not.toHaveBeenCalled()
  })
})

describe("RtaHero — a real flow object renders YNAB's own flat flow framing", () => {
  const FLOW = { prev_month: '2026-07', carried_in: 500, inflow: 300, cash_overspending_prev: 50 }
  // carriedIn + inflow - assigned - cashOverspendingPrev - assignedInFuture = 500+300-200-50-0
  const RTA = 550

  it('shows the title, every row and the total — with the year stripped from month names', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const panel = openPanel({
      ...BASE_PROPS,
      flow: FLOW,
      totalAssigned: 200,
      rta: RTA,
      // Unused on the flow path (stockLines is computed but never rendered), but must still
      // close the STOCK identity exactly — otherwise buildRtaLines's own residual check warns.
      onBudgetCash: RTA,
    })
    expect(panel.textContent).toContain('Ready to Assign Breakdown')
    expect(panel.querySelectorAll('.pl-rtapop-sec')).toHaveLength(0)
    expect(panel.textContent).toContain('Ready to Assign left over from July')
    expect(panel.textContent).not.toContain('from July 2026')
    expect(panel.textContent).toContain('Inflow: Ready to Assign transactions in August')
    expect(panel.textContent).not.toContain('in August 2026')
    expect(panel.textContent).toContain('Assigned in August')
    expect(panel.textContent).toContain('Cash overspending in July')
    expect(panel.textContent).toContain('Total Ready to Assign')
    expect(panel.textContent).not.toContain('is held until month-end')
    expect(warn).not.toHaveBeenCalled()
  })

  it('renders a dedicated glyph+subtotal header per sign group; item rows carry no glyph', () => {
    const panel = openPanel({
      ...BASE_PROPS,
      flow: FLOW,
      totalAssigned: 200,
      rta: RTA,
      onBudgetCash: RTA,
    })
    const rows = [...panel.querySelectorAll('.pl-rtapop-row')]
    const headers = rows.filter((r) => r.classList.contains('pl-rtapop-row--sec'))
    // Added: carried_in(500) + inflow(300) = 800. Deducted: -assigned(200) - cashOverPrev(50) = -250.
    expect(headers).toHaveLength(2)
    expect(headers[0]?.querySelector('.op')?.textContent).toBe('+')
    expect(headers[0]?.querySelector('.lbl')?.textContent).toBe('')
    expect(headers[0]?.querySelector('.val')?.textContent).toBe('S$800.00')
    expect(headers[1]?.querySelector('.op')?.textContent).toBe('−')
    expect(headers[1]?.querySelector('.val')?.textContent).toBe('S$250.00')

    const rowFor = (label: string) =>
      rows.find((r) => r.querySelector('.lbl')!.textContent === label)!
    const carried = rowFor('Ready to Assign left over from July')
    const inflow = rowFor('Inflow: Ready to Assign transactions in August')
    const assigned = rowFor('Assigned in August')
    const cashOver = rowFor('Cash overspending in July')
    for (const row of [carried, inflow, assigned, cashOver]) {
      expect(row.classList.contains('pl-rtapop-row--sec')).toBe(false)
      expect(row.querySelector('.op')!.textContent).toBe('')
    }
    expect(carried.querySelector('.val')!.textContent).toBe('+S$500.00')
    expect(inflow.querySelector('.val')!.textContent).toBe('+S$300.00')
    expect(assigned.querySelector('.val')!.textContent).toBe('−S$200.00')
    expect(cashOver.querySelector('.val')!.textContent).toBe('−S$50.00')
  })

  it('hides "Ready to Assign left over from {Prev}" when carried_in is exactly 0', () => {
    const panel = openPanel({
      ...BASE_PROPS,
      flow: { ...FLOW, carried_in: 0 },
      totalAssigned: 200,
      rta: 50, // 0 + 300 - 200 - 50 - 0
      onBudgetCash: 50,
    })
    expect(panel.textContent).not.toContain('Ready to Assign left over from')
  })

  it('shows the coaching line and the permanent info banner when RTA is positive', () => {
    const panel = openPanel({
      ...BASE_PROPS,
      flow: FLOW,
      totalAssigned: 200,
      rta: RTA,
      onBudgetCash: RTA,
    })
    expect(panel.textContent).toContain(
      'Give these dollars a job by assigning them to one or more envelopes!',
    )
    expect(panel.textContent).toContain(
      "Ready to Assign is money that hasn't yet been assigned to an envelope.",
    )
    expect(panel.textContent).not.toContain('YNAB Checkup')
  })

  it('hides the coaching line when RTA is not positive, but keeps the info banner', () => {
    const panel = openPanel({
      ...BASE_PROPS,
      flow: { prev_month: '2026-07', carried_in: -50, inflow: 20, cash_overspending_prev: 30 },
      totalAssigned: 0,
      rta: -60, // -50 + 20 - 0 - 30 - 0
      onBudgetCash: -60,
    })
    expect(panel.textContent).not.toContain('Give these dollars a job')
    expect(panel.textContent).toContain(
      "Ready to Assign is money that hasn't yet been assigned to an envelope.",
    )
  })

  it('renders every row, with no grouping headers, regardless of sign', () => {
    const NEGATIVE_FLOW = {
      prev_month: '2026-07',
      carried_in: -50,
      inflow: 20,
      cash_overspending_prev: 30,
    }
    const panel = openPanel({
      ...BASE_PROPS,
      flow: NEGATIVE_FLOW,
      totalAssigned: 0,
      rta: -60, // -50 + 20 - 0 - 30 - 0
      onBudgetCash: -60,
    })
    expect(panel.querySelectorAll('.pl-rtapop-sec')).toHaveLength(0)
    expect(panel.textContent).toContain('Ready to Assign left over from July')
  })

  it('hides "Cash overspending in {Prev}" when the previous month had none', () => {
    const panel = openPanel({
      ...BASE_PROPS,
      flow: { ...FLOW, cash_overspending_prev: 0 },
      totalAssigned: 200,
      rta: 600, // 500 + 300 - 200 - 0 - 0
      onBudgetCash: 600,
    })
    expect(panel.textContent).not.toContain('Cash overspending in July')
  })

  it('never shows a forward-looking footnote — real YNAB has none', () => {
    const panel = openPanel({
      ...BASE_PROPS,
      flow: FLOW,
      totalAssigned: 200,
      rta: RTA,
      onBudgetCash: RTA,
    })
    expect(panel.textContent).not.toContain('is held until month-end')
  })
})
