// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'

const setShowAllHistory = vi.fn()
const useIncomeView = vi.fn()

vi.mock('./useIncomeView', () => ({ useIncomeView: () => useIncomeView() }))

import { IncomeTab } from './IncomeTab'

function baseView() {
  return {
    loading: false,
    error: false,
    hasForecast: false,
    hasSummary: true,
    hasCalendar: false,
    hasAny: true,
    cards: [],
    sourceRows: [],
    restSrcCount: 0,
    restSrcSum: 0,
    restSrcBarPct: 0,
    totalSourceCount: 0,
    growth: null,
    incomeBaseTotalsAvailable: true,
    calendarHorizonDays: 90,
    calendarCount: 0,
    calendarTotalFmt: 'S$0.00',
    nextPayment: null,
    paymentRows: [],
    totalPaymentCount: 0,
    showAllSrc: false,
    setShowAllSrc: vi.fn(),
    showAllSrc_allRows: [],
    showAllPay: false,
    setShowAllPay: vi.fn(),
    allPaymentRows: [],
    showAllHistory: false,
    setShowAllHistory,
    retry: vi.fn(),
  }
}

describe('IncomeTab recent income', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('renders dividend, interest, and distribution events with cash versus reinvested delivery', () => {
    const historyRows = [
      {
        eventId: 'inc_3',
        date: '2026-08-03',
        symbol: 'ONWAI',
        name: 'ONWAI Inc',
        kindLabel: 'Dividend',
        amountBase: 125,
        deliveryLabel: 'Cash',
      },
      {
        eventId: 'inc_2',
        date: '2026-08-02',
        symbol: 'SSB',
        name: 'Singapore Savings Bond',
        kindLabel: 'Interest',
        amountBase: 80,
        deliveryLabel: 'Cash',
      },
      {
        eventId: 'inc_1',
        date: '2026-08-01',
        symbol: 'VWRA',
        name: 'Vanguard FTSE All-World',
        kindLabel: 'Distribution',
        amountBase: 42,
        deliveryLabel: 'Reinvested',
      },
    ]
    useIncomeView.mockReturnValue({
      ...baseView(),
      historyRows,
      allHistoryRows: historyRows,
      totalHistoryCount: historyRows.length,
    })

    render(<IncomeTab />)

    expect(screen.getByText('Recent income')).toBeTruthy()
    expect(screen.getByText('Dividend')).toBeTruthy()
    expect(screen.getByText('Interest')).toBeTruthy()
    expect(screen.getByText('Distribution')).toBeTruthy()
    expect(screen.getAllByText('Cash')).toHaveLength(2)
    expect(screen.getByText('Reinvested')).toBeTruthy()
  })

  it('offers the complete history when more than eight events exist', () => {
    const rows = Array.from({ length: 9 }, (_, index) => ({
      eventId: `inc_${index}`,
      date: `2026-07-${String(20 - index).padStart(2, '0')}`,
      symbol: 'ONWAI',
      name: 'ONWAI Inc',
      kindLabel: 'Dividend',
      amountBase: 10 + index,
      deliveryLabel: 'Cash',
    }))
    let currentView = {
      ...baseView(),
      historyRows: rows.slice(0, 8),
      allHistoryRows: rows,
      totalHistoryCount: rows.length,
    }
    setShowAllHistory.mockImplementation((update) => {
      const next = typeof update === 'function' ? update(currentView.showAllHistory) : update
      currentView = { ...currentView, showAllHistory: next }
    })
    useIncomeView.mockImplementation(() => currentView)

    const rendered = render(<IncomeTab />)
    expect(screen.getAllByText('ONWAI')).toHaveLength(8)
    fireEvent.click(screen.getByRole('button', { name: 'Show all 9 income events' }))
    rendered.rerender(<IncomeTab />)

    expect(screen.getAllByText('ONWAI')).toHaveLength(9)
    expect(screen.getByRole('button', { name: 'Show top 8 only' })).toBeTruthy()
  })

  it('shows the native net amount when a base FX rate is unavailable', () => {
    const historyRows = [
      {
        eventId: 'inc_fx',
        date: '2026-08-03',
        symbol: 'QAHIST',
        name: 'QA holding',
        kindLabel: 'Interest',
        amountBase: null,
        amountBaseAvailable: false,
        amountNative: 10,
        amountCurrency: 'USD',
        deliveryLabel: 'Cash',
      },
    ]
    useIncomeView.mockReturnValue({
      ...baseView(),
      historyRows,
      allHistoryRows: historyRows,
      totalHistoryCount: 1,
    })

    render(<IncomeTab />)

    expect(screen.getByText('$10.00')).toBeTruthy()
    expect(screen.queryByText('S$10.00')).toBeNull()
    expect(screen.getByText(/FX unavailable/)).toBeTruthy()
    expect(
      screen.getByTitle('Base-currency FX unavailable; showing the native net amount'),
    ).toBeTruthy()
  })

  it('shows an honest empty recent-income state when the tab has forecast data only', () => {
    useIncomeView.mockReturnValue({
      ...baseView(),
      hasForecast: true,
      hasSummary: false,
      historyRows: [],
      allHistoryRows: [],
      totalHistoryCount: 0,
    })

    render(<IncomeTab />)

    expect(screen.getByText('No income recorded yet')).toBeTruthy()
    expect(
      screen.getByText(/Record a dividend, interest payment, distribution, or DRP/),
    ).toBeTruthy()
  })
})
