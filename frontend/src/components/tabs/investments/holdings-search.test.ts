// @vitest-environment jsdom
/**
 * Regression tests for the Holdings search / filter / grouping correctness bugs:
 *
 *   BUG 1 — Non-unique React keys caused ghost DOM nodes: when the same instrument
 *            had multiple positions (vested + unvested, or same symbol in
 *            multiple accounts with the same name) the old key `r.sym + r.account`
 *            was identical for all of them. React's reconciler would fail to remove
 *            stale DOM nodes when the list was filtered, causing non-matching rows to
 *            remain visible alongside the single matching row.
 *   FIX 1  — BookRow.rowKey uses position_id (backend-assigned, globally unique) with
 *            a sym+account+vesting_state fallback so keys are always distinct.
 *
 *   BUG 2 — buildGroups could receive the full unfiltered d.all instead of the
 *            search-narrowed filteredRows; this test asserts the correct data-flow
 *            invariant: group row counts equal the filtered input, never more.
 *
 * Tests exercise the pure utility functions (buildGroups, sortRows) and the
 * BookRow shape produced by buildBook-equivalent logic without React.
 */

import { describe, it, expect } from 'vitest'
import type { ComputedRow } from '../../../types/portfolio'
import { buildGroups, sortRows } from './useInvestmentsBook'

// ── Minimal ComputedRow factory ────────────────────────────────────────────────

function makeRow(
  overrides: Partial<ComputedRow> & { ticker: string; asset_class: ComputedRow['asset_class'] },
): ComputedRow {
  return {
    category: overrides.account_name ?? 'Vantage Brokerage',
    label: overrides.ticker,
    vesting_state: overrides.vesting_state ?? 'vested',
    liquidity: overrides.liquidity ?? 'liquid',
    quantity: '100',
    price: '10',
    cost_basis: '8',
    currency: 'USD',
    note: '',
    symbol: overrides.symbol ?? overrides.ticker,
    asset_subclass: overrides.asset_subclass ?? 'stock',
    position_id: overrides.position_id,
    account_id: overrides.account_id ?? 'acct1',
    account_name: overrides.account_name ?? 'Vantage Brokerage',
    quote_currency: 'USD',
    _base: overrides._base ?? 1000,
    _pct: 0,
    _cost_base: 800,
    _disp_ccy: 'USD',
    _disp_unit_price: 10,
    _disp_unit_cost: 8,
    _pl: 200,
    _plpct: 25,
    _daily_pl: 10,
    _daily_plpct: 1,
    _prev: null,
    ...overrides,
  } as ComputedRow
}

// ── BookRow factory (mirrors buildBook's mapping) ──────────────────────────────
// We call the real buildGroups/sortRows with manually built BookRows to test
// the filter and key invariants without spinning up the hook.

import type { BookRow } from './useInvestmentsBook'

function makeBookRow(raw: ComputedRow, grand: number): BookRow {
  const sym = raw.symbol || raw.ticker || raw.label
  const account = raw.account_name || raw.category
  const rowKey = raw.position_id ?? `${sym}\0${account}\0${raw.vesting_state}`
  const value = raw._base
  return {
    rowKey,
    sym,
    name: raw.label,
    value,
    wt: grand > 0 ? (value / grand) * 100 : 0,
    pl: raw._pl ?? 0,
    plpct: raw._plpct ?? 0,
    day: raw._daily_plpct ?? 0,
    cls: raw.asset_class,
    sub: raw.asset_subclass || raw.asset_class,
    account,
    ccy: raw.quote_currency || raw.currency,
    qty: parseFloat(raw.quantity) || 0,
    price: raw._disp_unit_price,
    cost: raw._disp_unit_cost ?? 0,
    coupon: null,
    maturity: null,
    ytm: null,
    mergedCount: 1,
    _raw: raw,
  }
}

// ── Helpers ────────────────────────────────────────────────────────────────────

function buildBookRows(rawRows: ComputedRow[]): BookRow[] {
  const grand = rawRows.reduce((s, r) => s + r._base, 0)
  return rawRows.map((r) => makeBookRow(r, grand))
}

function searchFilter(rows: BookRow[], query: string): BookRow[] {
  if (!query) return rows
  const q = query.toLowerCase()
  return rows.filter((r) => (r.sym + ' ' + r.name).toLowerCase().includes(q))
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('buildGroups — no row duplication', () => {
  it('each input row appears in exactly one group (asset_class grouping)', () => {
    const raw = [
      makeRow({ ticker: 'SPCX', asset_class: 'equity', position_id: 'pos-spcx', _base: 238.71 }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-liq',
        vesting_state: 'vested',
        _base: 500,
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-locked',
        vesting_state: 'unvested',
        _base: 200,
      }),
      makeRow({ ticker: 'SGB', asset_class: 'bond', position_id: 'pos-sgb', _base: 1000 }),
    ]
    const rows = buildBookRows(raw)
    const groups = buildGroups(rows, 'asset_class', 1938.71)

    // All rows should appear in groups, and the total count should equal input length
    const totalRowsInGroups = groups.reduce((s, g) => s + g.rows.length, 0)
    expect(totalRowsInGroups).toBe(raw.length)

    // No row should appear in more than one group
    const seen = new Set<string>()
    for (const g of groups) {
      for (const r of g.rows) {
        expect(seen.has(r.rowKey)).toBe(false)
        seen.add(r.rowKey)
      }
    }
  })

  it('groupBy=none — one group with all rows, no duplication', () => {
    const raw = [
      makeRow({ ticker: 'SPCX', asset_class: 'equity', position_id: 'pos-spcx', _base: 100 }),
      makeRow({ ticker: 'ONWAI', asset_class: 'equity', position_id: 'pos-onwai', _base: 200 }),
    ]
    const rows = buildBookRows(raw)
    const groups = buildGroups(rows, 'none', 300)

    expect(groups).toHaveLength(1)
    expect(groups[0]!.rows).toHaveLength(2)
  })
})

describe('search filter + buildGroups — rendered rows must exactly equal filtered set', () => {
  it('searching "spcx" with mixed equity portfolio returns ONLY spcx', () => {
    const raw = [
      makeRow({ ticker: 'SPCX', asset_class: 'equity', position_id: 'pos-spcx', _base: 238.71 }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-liq',
        vesting_state: 'vested',
        _base: 500,
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-locked',
        vesting_state: 'unvested',
        _base: 200,
        account_name: 'Vantage Brokerage',
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-2',
        _base: 150,
        account_name: 'Vantage Brokerage',
      }),
      makeRow({ ticker: 'SGB', asset_class: 'bond', position_id: 'pos-sgb', _base: 1000 }),
    ]
    const allRows = buildBookRows(raw)

    // Simulate the InvestmentsTab filter pipeline
    const baseRows = searchFilter(allRows, 'spcx')
    const filteredRows = baseRows // no class-chip filter for this test (all)
    const groups = buildGroups(
      filteredRows,
      'asset_class',
      allRows.reduce((s, r) => s + r.value, 0),
    )

    // Only SPCX should be in the result
    expect(filteredRows).toHaveLength(1)
    expect(filteredRows[0]!.sym).toBe('SPCX')

    // The groups passed to FeedCards must contain only the filtered row
    const totalRowsInGroups = groups.reduce((s, g) => s + g.rows.length, 0)
    expect(totalRowsInGroups).toBe(1)
    expect(groups[0]!.rows[0]!.sym).toBe('SPCX')

    // Group subtotal equals SPCX value only — not the total of all holdings
    expect(groups[0]!.subtotal).toBeCloseTo(238.71, 1)
  })

  it('searching "onwai" returns all 3 ONWAI positions with distinct rowKeys', () => {
    const raw = [
      makeRow({ ticker: 'SPCX', asset_class: 'equity', position_id: 'pos-spcx', _base: 238.71 }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-liq',
        vesting_state: 'vested',
        _base: 500,
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-locked',
        vesting_state: 'unvested',
        _base: 200,
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-onwai-alt',
        _base: 150,
        account_name: 'Vantage Brokerage',
      }),
    ]
    const allRows = buildBookRows(raw)

    const baseRows = searchFilter(allRows, 'onwai')
    const groups = buildGroups(
      baseRows,
      'asset_class',
      allRows.reduce((s, r) => s + r.value, 0),
    )

    expect(baseRows).toHaveLength(3)

    // All rowKeys must be unique
    const keys = baseRows.map((r) => r.rowKey)
    expect(new Set(keys).size).toBe(3)

    // Groups contain exactly the filtered rows
    const totalRowsInGroups = groups.reduce((s, g) => s + g.rows.length, 0)
    expect(totalRowsInGroups).toBe(3)
  })

  it('empty search shows all rows without duplication', () => {
    const raw = [
      makeRow({ ticker: 'SPCX', asset_class: 'equity', position_id: 'pos-spcx', _base: 100 }),
      makeRow({ ticker: 'ONWAI', asset_class: 'equity', position_id: 'pos-onwai', _base: 200 }),
      makeRow({ ticker: 'SGB', asset_class: 'bond', position_id: 'pos-sgb', _base: 1000 }),
    ]
    const allRows = buildBookRows(raw)

    const baseRows = searchFilter(allRows, '')
    const groups = buildGroups(baseRows, 'asset_class', 1300)

    expect(baseRows).toHaveLength(3)
    const totalRowsInGroups = groups.reduce((s, g) => s + g.rows.length, 0)
    expect(totalRowsInGroups).toBe(3)
  })

  it('class-chip filter (equity only) narrows to matching class', () => {
    const raw = [
      makeRow({ ticker: 'SPCX', asset_class: 'equity', position_id: 'pos-spcx', _base: 100 }),
      makeRow({ ticker: 'ONWAI', asset_class: 'equity', position_id: 'pos-onwai', _base: 200 }),
      makeRow({ ticker: 'SGB', asset_class: 'bond', position_id: 'pos-sgb', _base: 1000 }),
    ]
    const allRows = buildBookRows(raw)

    // No text search, but equity class chip active
    const baseRows = searchFilter(allRows, '')
    const filteredRows = baseRows.filter((r) => r.cls === 'equity')
    const groups = buildGroups(filteredRows, 'asset_class', 1300)

    expect(filteredRows).toHaveLength(2)
    const totalRowsInGroups = groups.reduce((s, g) => s + g.rows.length, 0)
    expect(totalRowsInGroups).toBe(2)
    // Only the equity group should be present
    expect(groups.every((g) => g.cls === 'equity')).toBe(true)
  })
})

describe('BookRow.rowKey uniqueness', () => {
  it('liquid + locked positions for same instrument get distinct rowKeys when position_id present', () => {
    const raw = [
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-liq',
        vesting_state: 'vested',
        _base: 500,
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: 'pos-locked',
        vesting_state: 'unvested',
        _base: 200,
      }),
    ]
    const rows = buildBookRows(raw)
    expect(rows[0]!.rowKey).toBe('pos-liq')
    expect(rows[1]!.rowKey).toBe('pos-locked')
    expect(rows[0]!.rowKey).not.toBe(rows[1]!.rowKey)
  })

  it('fallback rowKey is entitlement-distinguishing even without position_id', () => {
    const raw = [
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: undefined,
        vesting_state: 'vested',
        _base: 500,
      }),
      makeRow({
        ticker: 'ONWAI',
        asset_class: 'equity',
        position_id: undefined,
        vesting_state: 'unvested',
        _base: 200,
      }),
    ]
    const rows = buildBookRows(raw)
    expect(rows[0]!.rowKey).not.toBe(rows[1]!.rowKey)
  })
})

describe('sortRows — preserves all rows, no duplication', () => {
  it('sort by value preserves row count', () => {
    const raw = [
      makeRow({ ticker: 'A', asset_class: 'equity', position_id: 'p1', _base: 100 }),
      makeRow({ ticker: 'B', asset_class: 'equity', position_id: 'p2', _base: 300 }),
      makeRow({ ticker: 'C', asset_class: 'bond', position_id: 'p3', _base: 200 }),
    ]
    const rows = buildBookRows(raw)
    const sorted = sortRows(rows, 'value', -1)
    expect(sorted).toHaveLength(3)
    expect(sorted[0]!.sym).toBe('B')
    expect(sorted[1]!.sym).toBe('C')
    expect(sorted[2]!.sym).toBe('A')
  })

  it('sort by sym ascending preserves row count', () => {
    const raw = [
      makeRow({ ticker: 'ZCASH', asset_class: 'crypto', position_id: 'p1', _base: 100 }),
      makeRow({ ticker: 'APPLE', asset_class: 'equity', position_id: 'p2', _base: 200 }),
    ]
    const rows = buildBookRows(raw)
    const sorted = sortRows(rows, 'sym', 1)
    expect(sorted).toHaveLength(2)
    expect(sorted[0]!.sym).toBe('APPLE')
  })
})
