// @vitest-environment jsdom
import { renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import {
  mergeByInstrument,
  shouldMergeAcrossAccounts,
  useInvestmentsBook,
  type BookRow,
} from './useInvestmentsBook'
import type { InvestedBook, PortfolioAnalytics } from '../../../types/aggregates'

const { usePortfolioAnalytics } = vi.hoisted(() => ({ usePortfolioAnalytics: vi.fn() }))
vi.mock('../../../hooks/serverState', () => ({ usePortfolioAnalytics }))

function row(overrides: Partial<BookRow> = {}): BookRow {
  return {
    rowKey: 'p1',
    sym: 'ONWAI',
    name: 'ONWAI Inc',
    value: 1000,
    wt: 10,
    pl: 100,
    plpct: 10,
    day: 0,
    cls: 'equity',
    sub: 'equity',
    account: 'Meridian Bank',
    ccy: 'EUR',
    qty: 10,
    price: 100,
    cost: 90,
    coupon: null,
    maturity: null,
    ytm: null,
    mergedCount: 1,
    _raw: { instrument_id: 'onwai', symbol: 'ONWAI', vesting_state: 'vested' } as never,
    ...overrides,
  }
}

describe('shouldMergeAcrossAccounts', () => {
  it('merges when grouped by asset class / currency / none and no account filter', () => {
    expect(shouldMergeAcrossAccounts('asset_class', '')).toBe(true)
    expect(shouldMergeAcrossAccounts('currency', '')).toBe(true)
    expect(shouldMergeAcrossAccounts('none', '')).toBe(true)
  })

  it('never merges when grouped by account — each group is already one account', () => {
    expect(shouldMergeAcrossAccounts('account', '')).toBe(false)
  })

  it('never merges when an explicit account filter is active, regardless of grouping', () => {
    expect(shouldMergeAcrossAccounts('asset_class', 'Meridian Bank Workplace Solutions')).toBe(
      false,
    )
    expect(shouldMergeAcrossAccounts('none', 'Meridian Bank Workplace Solutions')).toBe(false)
  })
})

describe('InvestmentsTab account-filter regression (2026-08-26)', () => {
  it("filtering to one account must show that account's own value, not the cross-account merge", () => {
    // The full, unfiltered book: ONWAI held at Meridian Bank (400) AND Vantage Brokerage (600) — server-merged total 1000.
    const jpMorganRow = row({ rowKey: 'p-jpm', account: 'Meridian Bank', value: 400 })
    const ibkrRow = row({ rowKey: 'p-ibkr', account: 'Vantage Brokerage', value: 600 })
    const mergedRow = row({
      rowKey: 'merged:onwai',
      account: '2 accounts',
      value: 1000,
      mergedCount: 2,
    })

    // InvestmentsTab.tsx's own baseRows step: filter to "Meridian Bank" BEFORE any merge decision.
    const acct = 'Meridian Bank'
    const baseRows = [jpMorganRow, ibkrRow].filter((r) => r.account === acct)
    expect(baseRows).toEqual([jpMorganRow])

    // The bug: calling mergeByInstrument unconditionally substitutes the global merged row back
    // in, even though baseRows was already scoped to one account.
    const buggy = mergeByInstrument(baseRows, [mergedRow])
    expect(buggy[0]?.value).toBe(1000) // wrong: this is Meridian Bank + Vantage Brokerage combined

    // The fix: an active account filter must skip the merge step entirely.
    const effGroupBy = 'none'
    const fixed = shouldMergeAcrossAccounts(effGroupBy, acct)
      ? mergeByInstrument(baseRows, [mergedRow])
      : baseRows
    expect(fixed).toEqual([jpMorganRow])
    expect(fixed[0]?.value).toBe(400) // right: Meridian Bank's own share only
  })
})

// ── MATH-37 / MATH-38 — server-computed drift, never rounded before it's used ──────────────

function invBook(overrides: Partial<InvestedBook> = {}): InvestedBook {
  return {
    base_currency: 'SGD',
    is_live: true,
    grand: 1_000_000,
    empty: false,
    portfolio_total: 1_000_000,
    pct_of_portfolio: 100,
    rows: [],
    merged_rows: [],
    sections: [
      {
        cls: 'equity',
        subtotal: 326_000,
        current_pct: 32.6,
        target_pct: 29.4,
        drift_pp: 3.2,
        trade_base: -32_000,
        row_keys: [],
      },
    ],
    today_pl: 0,
    today_pct: 0,
    total_pl: 0,
    total_pl_pct: 0,
    movers: [],
    concentration: [],
    concentration_guideline_pct: 20,
    top_name: null,
    threshold_pp: 5,
    allocation: [
      {
        cls: 'equity',
        current_pct: 32.6,
        target_pct: 29.4,
        drift_pp: 3.2,
        trade_base: -32_000,
        subtotal: 326_000,
      },
    ],
    ...overrides,
  }
}

describe('useInvestmentsBook — MATH-37/38 server drift is never re-derived', () => {
  it('carries the unrounded drift_pp and the server trade_base alongside the rounded display pct', () => {
    usePortfolioAnalytics.mockReturnValue({
      data: { book: invBook() } as PortfolioAnalytics,
      initialLoading: false,
    })

    const { result } = renderHook(() => useInvestmentsBook([]))

    expect(result.current.alloc).toEqual([
      { cls: 'equity', cur: 33, tgt: 29, driftPp: 3.2, tradeBase: -32_000 },
    ])
    // The server's own threshold — never a number hardcoded on the client.
    expect(result.current.thresholdPp).toBe(5)
  })

  it('falls back to 5pp only while the analytics read is absent', () => {
    usePortfolioAnalytics.mockReturnValue({ data: undefined, initialLoading: true })

    const { result } = renderHook(() => useInvestmentsBook([]))

    expect(result.current.thresholdPp).toBe(5)
  })
})

describe('sortRows — an unknown P&L sorts last in either direction', () => {
  it('keeps null pl/plpct at the bottom whether ascending or descending', async () => {
    const { sortRows } = await import('./useInvestmentsBook')
    const rows = [
      row({ rowKey: 'unk', pl: null, plpct: null }),
      row({ rowKey: 'gain', pl: 100, plpct: 10 }),
      row({ rowKey: 'loss', pl: -50, plpct: -5 }),
    ]
    expect(sortRows(rows, 'pl', -1).map((r) => r.rowKey)).toEqual(['gain', 'loss', 'unk'])
    expect(sortRows(rows, 'pl', 1).map((r) => r.rowKey)).toEqual(['loss', 'gain', 'unk'])
    expect(sortRows(rows, 'plpct', -1).map((r) => r.rowKey)).toEqual(['gain', 'loss', 'unk'])
    expect(sortRows(rows, 'plpct', 1).map((r) => r.rowKey)).toEqual(['loss', 'gain', 'unk'])
  })
})
