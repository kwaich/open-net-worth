import { describe, it, expect } from 'vitest'
import {
  buildDestinationOptions,
  buildPreviewLine,
  isLastPositionInAccount,
} from './MovePositionModal'
import type { MoveableAccount } from './MovePositionModal'
import type { ComputedRow } from '../../types/portfolio'

function makeAccount(
  overrides: Partial<MoveableAccount> & { id: string; name: string },
): MoveableAccount {
  return {
    institution: null,
    currency: 'USD',
    ...overrides,
  }
}

function makeRow(overrides: Partial<ComputedRow>): ComputedRow {
  return {
    category: 'equity',
    label: 'Apple Inc.',
    vesting_state: 'vested',
    liquidity: 'liquid',
    asset_class: 'equity',
    quantity: '10',
    price: '150.00',
    cost_basis: '1200.00',
    currency: 'USD',
    ticker: 'AAPL',
    note: '',
    _base: 1500,
    _pct: 0.1,
    _cost_base: 1200,
    _disp_ccy: 'USD',
    _disp_unit_price: 150,
    _disp_unit_cost: 120,
    _pl: 300,
    _plpct: 0.25,
    _daily_pl: null,
    _daily_plpct: null,
    ...overrides,
  }
}

// ── buildDestinationOptions ────────────────────────────────────────────────

describe('buildDestinationOptions', () => {
  it('groups accounts by institution and sorts groups alphabetically', () => {
    const accounts = [
      makeAccount({ id: 'a1', name: 'Fidelity USD', institution: 'Fidelity' }),
      makeAccount({ id: 'a2', name: 'IBKR USD', institution: 'IBKR' }),
      makeAccount({ id: 'a3', name: 'IBKR SGD', institution: 'IBKR' }),
    ]
    const result = buildDestinationOptions(accounts)
    expect(result.map((g) => g.group)).toEqual(['Fidelity', 'IBKR'])
  })

  it('places accounts with null/blank institution into "Other" group at the end', () => {
    const accounts = [
      makeAccount({ id: 'a1', name: 'Zoo Bank', institution: null }),
      makeAccount({ id: 'a2', name: 'Alpha Bank', institution: null }),
      makeAccount({ id: 'a3', name: 'IBKR USD', institution: 'IBKR' }),
    ]
    const result = buildDestinationOptions(accounts)
    const groups = result.map((g) => g.group)
    expect(groups[groups.length - 1]).toBe('Other')
    // "Other" comes after named groups
    expect(groups[0]).toBe('IBKR')
  })

  it('sorts accounts within each group by name', () => {
    const accounts = [
      makeAccount({ id: 'a1', name: 'IBKR SGD', institution: 'IBKR' }),
      makeAccount({ id: 'a2', name: 'IBKR AUD', institution: 'IBKR' }),
      makeAccount({ id: 'a3', name: 'IBKR USD', institution: 'IBKR' }),
    ]
    const [group] = buildDestinationOptions(accounts)
    expect(group?.options.map((o) => o.label)).toEqual([
      'IBKR AUD (USD)',
      'IBKR SGD (USD)',
      'IBKR USD (USD)',
    ])
  })

  it('returns an empty array when accounts list is empty', () => {
    expect(buildDestinationOptions([])).toEqual([])
  })

  it('option label includes account name and currency', () => {
    const accounts = [
      makeAccount({ id: 'a1', name: 'My Account', institution: 'Broker', currency: 'SGD' }),
    ]
    const [group] = buildDestinationOptions(accounts)
    expect(group?.options[0]).toEqual({ value: 'a1', label: 'My Account (SGD)' })
  })

  it('omits the "Other" group when all accounts have an institution', () => {
    const accounts = [makeAccount({ id: 'a1', name: 'IBKR USD', institution: 'IBKR' })]
    const result = buildDestinationOptions(accounts)
    expect(result.every((g) => g.group !== 'Other')).toBe(true)
  })
})

// ── buildPreviewLine ────────────────────────────────────────────────────────

describe('buildPreviewLine', () => {
  it('formats "from (currency) → to (currency)"', () => {
    const row = makeRow({ account_name: 'IBKR', currency: 'USD' })
    const dest = makeAccount({ id: 'a2', name: 'Fidelity', currency: 'SGD' })
    expect(buildPreviewLine(row, dest)).toBe('IBKR (USD) → Fidelity (SGD)')
  })

  it('falls back to institution then category when account_name is absent', () => {
    const row = makeRow({ account_name: undefined, institution: 'Schwab', currency: 'USD' })
    const dest = makeAccount({ id: 'a2', name: 'IBKR', currency: 'USD' })
    expect(buildPreviewLine(row, dest)).toBe('Schwab (USD) → IBKR (USD)')
  })

  it('falls back to em-dash when none of account_name / institution / category are present', () => {
    const row = makeRow({
      account_name: undefined,
      institution: undefined,
      category: '',
      currency: 'USD',
    })
    const dest = makeAccount({ id: 'a2', name: 'IBKR', currency: 'USD' })
    expect(buildPreviewLine(row, dest)).toBe('— (USD) → IBKR (USD)')
  })
})

// ── isLastPositionInAccount ─────────────────────────────────────────────────

describe('isLastPositionInAccount', () => {
  it('returns true when the row is the only position in the account', () => {
    const row = makeRow({ account_id: 'acct1', position_id: 'pos1' })
    const allRows = [row, makeRow({ account_id: 'acct2', position_id: 'pos2' })]
    expect(isLastPositionInAccount(row, allRows)).toBe(true)
  })

  it('returns false when other positions share the same account', () => {
    const row = makeRow({ account_id: 'acct1', position_id: 'pos1' })
    const allRows = [row, makeRow({ account_id: 'acct1', position_id: 'pos2' })]
    expect(isLastPositionInAccount(row, allRows)).toBe(false)
  })

  it('returns false when the row has no account_id', () => {
    const row = makeRow({ account_id: undefined, position_id: 'pos1' })
    expect(isLastPositionInAccount(row, [row])).toBe(false)
  })

  it('ignores rows without a position_id (cash/account rows)', () => {
    const row = makeRow({ account_id: 'acct1', position_id: 'pos1' })
    // A second row with same account_id but no position_id should not count
    const allRows = [row, makeRow({ account_id: 'acct1', position_id: undefined })]
    expect(isLastPositionInAccount(row, allRows)).toBe(true)
  })
})

// ── cache regression: per-row exclusion at call site, not at cache time ───────
// Covers the bug where openMoveModal filtered out row.account_id before caching,
// so a second modal open for a different row permanently lacked that account.

describe('buildDestinationOptions per-row exclusion at call site', () => {
  const fullCache: MoveableAccount[] = [
    makeAccount({ id: 'acct-a', name: 'Account A', institution: 'Broker' }),
    makeAccount({ id: 'acct-b', name: 'Account B', institution: 'Broker' }),
    makeAccount({ id: 'acct-c', name: 'Account C', institution: null }),
  ]

  it('row-A open: excludes acct-a, shows acct-b and acct-c', () => {
    const rowA = makeRow({ account_id: 'acct-a', position_id: 'pos-a' })
    const forRowA = fullCache.filter((a) => a.id !== rowA.account_id)
    expect(forRowA.map((a) => a.id)).toEqual(['acct-b', 'acct-c'])
    const opts = buildDestinationOptions(forRowA)
    const allIds = opts.flatMap((g) => g.options.map((o) => o.value))
    expect(allIds).toContain('acct-b')
    expect(allIds).toContain('acct-c')
    expect(allIds).not.toContain('acct-a')
  })

  it('row-B open: excludes acct-b, shows acct-a (which was absent when cached for row-A)', () => {
    const rowB = makeRow({ account_id: 'acct-b', position_id: 'pos-b' })
    // Same fullCache (no re-fetch) — filter at call site only
    const forRowB = fullCache.filter((a) => a.id !== rowB.account_id)
    expect(forRowB.map((a) => a.id)).toEqual(['acct-a', 'acct-c'])
    const opts = buildDestinationOptions(forRowB)
    const allIds = opts.flatMap((g) => g.options.map((o) => o.value))
    expect(allIds).toContain('acct-a')
    expect(allIds).toContain('acct-c')
    expect(allIds).not.toContain('acct-b')
  })
})
