// @vitest-environment jsdom
//
// The Dashboard's "Next 30 days" card reads the server's overdue and due-soon figures (the same
// ones the Commitments masthead and the daily briefing quote) and lists recurring income beside
// what goes out. Persona QA round 5: a salary landing in 11 days never appeared and the card
// said "Nothing due".

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const authFetch = vi.hoisted(() => vi.fn())
vi.mock('../../../../api/client', () => ({ authFetch }))

import { overviewCashflowCoreWidgetComponents } from './overviewCashflowCore'
import { WIDGET_DEFINITIONS } from '../definitions'
import type { WidgetInstance } from '../types'
import { setBaseCurrency } from '../../../../utils/format'

setBaseCurrency('USD')

afterEach(() => {
  cleanup()
  authFetch.mockReset()
})

function iso(offsetDays: number): string {
  const d = new Date()
  d.setHours(12, 0, 0, 0)
  d.setDate(d.getDate() + offsetDays)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function summary(over: Record<string, unknown>) {
  return {
    events: [],
    strip_days: 30,
    strip_total_base: 0,
    overdue: [],
    overdue_count: 0,
    overdue_total_base: 0,
    expected_income: [],
    expected_income_total_base: 0,
    ...over,
  }
}

async function renderUpcoming(
  body: Record<string, unknown>,
  onNavigate?: (tab: string) => void,
): Promise<string> {
  authFetch.mockImplementation(() =>
    Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(summary(body)) }),
  )
  const Comp = overviewCashflowCoreWidgetComponents.upcoming!
  const def = WIDGET_DEFINITIONS.find((d) => d.defId === 'upcoming')!
  const instance: WidgetInstance = {
    instanceId: 'w-upcoming',
    defId: 'upcoming',
    x: 0,
    y: 0,
    w: 6,
    h: 200,
    config: {},
  }
  const { container } = render(
    <Comp instance={instance} def={def} onPatch={vi.fn()} onNavigate={onNavigate} />,
  )
  await waitFor(() => expect(container.textContent).not.toMatch(/^\s*$/))
  await waitFor(() => expect(authFetch).toHaveBeenCalled())
  return container.textContent ?? ''
}

describe('Next 30 days', () => {
  it('shows what goes out, what is overdue, and the salary coming in', async () => {
    const text = await renderUpcoming({
      events: [
        {
          commitment_id: 'car',
          commitment_name: 'Car Loan',
          kind: 'installment',
          label: 'monthly debit',
          date: iso(1),
          amount_base: 1234,
        },
      ],
      strip_total_base: 4234,
      overdue_count: 3,
      overdue_total_base: 3000,
      expected_income: [{ date: iso(11), label: 'Salary', cadence: 'monthly', amount_base: 6000 }],
      expected_income_total_base: 6000,
    })

    await waitFor(() => expect(document.body.textContent).toContain('Salary'))
    const shown = document.body.textContent ?? text
    expect(shown).toContain('3 overdue')
    expect(shown).toContain('$3,000.00')
    expect(shown).toContain('$4,234.00 going out')
    expect(shown).toContain('$6,000.00 coming in')
    expect(shown).toContain('monthly debit')
    expect(shown).toContain('Recurring income')
  })

  it('lists recurring bills and card statements as money going out, beside the salary', async () => {
    await renderUpcoming({
      events: [
        {
          commitment_id: 'car',
          commitment_name: 'Car Loan',
          kind: 'installment',
          label: 'monthly debit',
          date: iso(1),
          amount_base: 1234,
        },
      ],
      strip_total_base: 1234,
      bills: [{ date: iso(4), label: 'Rent', cadence: 'monthly', amount_base: 2400 }],
      card_bills: [
        { date: iso(9), label: 'Visa', account_id: 'acc_v', amount_base: 640, estimated: false },
      ],
      going_out_total_base: 4274,
      expected_income: [{ date: iso(11), label: 'Salary', cadence: 'monthly', amount_base: 6000 }],
      expected_income_total_base: 6000,
    })

    await waitFor(() => expect(document.body.textContent).toContain('Rent'))
    const shown = document.body.textContent ?? ''
    expect(shown).toContain('$4,274.00 going out')
    expect(shown).toContain('Recurring bill')
    expect(shown).toContain('Visa')
    expect(shown).toContain('Card bill')
    // In is green, out is red — one colour per direction, whatever the kind.
    const dot = (label: string) =>
      screen
        .getByText(label, { exact: false })
        .closest('.ov-upcoming-row')!
        .querySelector<HTMLElement>('.ov-upcoming-dot')!.style.background
    expect(dot('Rent')).toBe('var(--bad)')
    expect(dot('Visa')).toBe('var(--bad)')
    expect(dot('monthly debit')).toBe('var(--bad)')
    expect(dot('Salary')).toBe('var(--good)')
  })

  it('says there are no dated bills, not S$0 going out, when the book has none and income is coming', async () => {
    const onNavigate = vi.fn()
    await renderUpcoming(
      {
        expected_income: [
          { date: iso(5), label: 'PayNow from Lee Wei', cadence: 'monthly', amount_base: 800 },
        ],
        expected_income_total_base: 800,
      },
      onNavigate,
    )

    await waitFor(() => expect(document.body.textContent).toContain('PayNow from Lee Wei'))
    const shown = document.body.textContent ?? ''
    expect(shown).toContain('No dated bills yet')
    expect(shown).toContain('$800.00 coming in')
    expect(shown).not.toContain('going out')
    fireEvent.click(screen.getByText('Add due dates'))
    expect(onNavigate).toHaveBeenCalledWith('commitments')
  })

  it('invites due dates when there is nothing at all to show', async () => {
    await renderUpcoming({})

    await waitFor(() => expect(document.body.textContent).toContain('No dated bills yet'))
    expect(document.body.textContent).not.toContain('going out')
  })

  it('keeps the going-out figure, even at zero, once the book has dated bills', async () => {
    await renderUpcoming({
      events: [
        {
          commitment_id: 'car',
          commitment_name: 'Car Loan',
          kind: 'installment',
          label: 'monthly debit',
          date: iso(60),
          amount_base: 1234,
        },
      ],
      expected_income: [{ date: iso(11), label: 'Salary', cadence: 'monthly', amount_base: 6000 }],
      expected_income_total_base: 6000,
    })

    await waitFor(() => expect(document.body.textContent).toContain('Salary'))
    expect(document.body.textContent).toContain('$0.00 going out')
    expect(document.body.textContent).not.toContain('No dated bills yet')
  })

  it('says nothing is due to pay when dated bills exist but none fall in the window', async () => {
    await renderUpcoming({
      events: [
        {
          commitment_id: 'car',
          commitment_name: 'Car Loan',
          kind: 'installment',
          label: 'monthly debit',
          date: iso(60),
          amount_base: 1234,
        },
      ],
    })

    await waitFor(() => expect(document.body.textContent).toContain('Nothing due to pay'))
  })
})
