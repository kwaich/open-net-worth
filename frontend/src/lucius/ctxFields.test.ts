// ctxFields.test.ts — unit tests for the write-side data-ctx-fields helper.
//
// Coverage:
//   1. buildCtxFields — drops null, undefined, empty string; keeps all other values
//   2. Per-type schemas: transaction, account, holding, category, payee
//   3. Key normalization: holding uses `value` (never `marketValue`)
//   4. No double-serialization: output is a valid JSON string

import { describe, it, expect } from 'vitest'
import {
  buildCtxFields,
  type TransactionCtxRecord,
  type AccountCtxRecord,
  type HoldingCtxRecord,
  type CategoryCtxRecord,
} from './ctxFields'

// ── buildCtxFields — null-drop behaviour ──────────────────────────────────────

describe('buildCtxFields — null-drop', () => {
  it('drops null values', () => {
    const result = JSON.parse(buildCtxFields({ id: 'x', payee: null }))
    expect(result).toEqual({ id: 'x' })
  })

  it('drops undefined values', () => {
    const result = JSON.parse(buildCtxFields({ id: 'x', note: undefined }))
    expect(result).toEqual({ id: 'x' })
  })

  it('drops empty-string values', () => {
    const result = JSON.parse(buildCtxFields({ id: 'x', note: '' }))
    expect(result).toEqual({ id: 'x' })
  })

  it('keeps 0 and false (falsy-but-not-null)', () => {
    const result = JSON.parse(buildCtxFields({ id: 'x', qty: 0, flag: false }))
    expect(result).toEqual({ id: 'x', qty: 0, flag: false })
  })

  it('returns valid JSON string', () => {
    const json = buildCtxFields({ id: 'a', name: 'Test' })
    expect(() => JSON.parse(json)).not.toThrow()
  })

  it('returns empty object JSON when all values are null/empty', () => {
    const result = JSON.parse(buildCtxFields({ payee: null, note: '' }))
    expect(result).toEqual({})
  })
})

// ── transaction ────────────────────────────────────────────────────────────────

describe('buildCtxFields — transaction', () => {
  it('produces the expected keys for a full expense transaction', () => {
    const rec: TransactionCtxRecord = {
      id: 'txn-1',
      date: '2026-06-18',
      payee: 'Grab',
      category: 'transport',
      amount: '−14.80 SGD',
      account: 'Meridian Multiplier',
      description: 'Grab ride — work trip',
      status: 'Cleared',
      kind: 'expense',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.id).toBe('txn-1')
    expect(result.date).toBe('2026-06-18')
    expect(result.payee).toBe('Grab')
    expect(result.category).toBe('transport')
    expect(result.amount).toBe('−14.80 SGD')
    expect(result.account).toBe('Meridian Multiplier')
    expect(result.description).toBe('Grab ride — work trip')
    expect(result.status).toBe('Cleared')
    expect(result.kind).toBe('expense')
  })

  it('drops optional nulls (payee, category, description)', () => {
    const rec: TransactionCtxRecord = {
      id: 'txn-2',
      date: '2026-06-01',
      payee: null,
      category: null,
      amount: '+2,000.00 SGD',
      account: 'Lighthouse Bank',
      description: null,
      status: 'Uncleared',
      kind: 'income',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.payee).toBeUndefined()
    expect(result.category).toBeUndefined()
    expect(result.description).toBeUndefined()
    expect(result.id).toBe('txn-2')
    expect(result.amount).toBe('+2,000.00 SGD')
  })

  it('stores transfer legs with from_account / to_account instead of account', () => {
    const rec: TransactionCtxRecord = {
      id: 'txn-3',
      date: '2026-05-10',
      amount: '+5,000.00 SGD',
      account: null,
      from_account: 'Meridian Multiplier',
      to_account: 'Lighthouse Bank 360',
      status: 'Cleared',
      kind: 'transfer',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.account).toBeUndefined()
    expect(result.from_account).toBe('Meridian Multiplier')
    expect(result.to_account).toBe('Lighthouse Bank 360')
  })
})

// ── account ────────────────────────────────────────────────────────────────────

describe('buildCtxFields — account', () => {
  it('produces the expected keys for a full account', () => {
    const rec: AccountCtxRecord = {
      id: 'acct-1',
      name: 'Vantage Brokerage USD',
      balance: 'S$48,200.00',
      type: 'Brokerage',
      custodian: 'Vantage Brokerage',
      currency: 'USD',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.id).toBe('acct-1')
    expect(result.name).toBe('Vantage Brokerage USD')
    expect(result.balance).toBe('S$48,200.00')
    expect(result.type).toBe('Brokerage')
    expect(result.custodian).toBe('Vantage Brokerage')
    expect(result.currency).toBe('USD')
  })

  it('drops custodian and currency when null', () => {
    const rec: AccountCtxRecord = {
      id: 'acct-2',
      name: 'Meridian Multiplier',
      balance: 'S$12,000.00',
      type: 'Savings',
      custodian: null,
      currency: null,
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.custodian).toBeUndefined()
    expect(result.currency).toBeUndefined()
  })
})

// ── holding ────────────────────────────────────────────────────────────────────
// The canonical key is `value` — never `marketValue` (that was the drifted alias).

describe('buildCtxFields — holding', () => {
  it('uses canonical "value" key (not marketValue)', () => {
    const rec: HoldingCtxRecord = {
      id: 'pos-1',
      symbol: 'AAPL',
      price: 'S$185.20',
      value: 'S$18,520.00',
      account: 'Vantage Brokerage',
      weight: '12.30%',
      day: '+0.70%',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.value).toBe('S$18,520.00')
    expect(result.marketValue).toBeUndefined()
    expect(result.price).toBe('S$185.20')
    expect(result.symbol).toBe('AAPL')
    expect(result.weight).toBe('12.30%')
    expect(result.day).toBe('+0.70%')
  })

  it('drops optional null fields (symbol, price, account, weight, day, qty, avgCost, uplBase, uplPct)', () => {
    const rec: HoldingCtxRecord = {
      id: 'pos-2',
      symbol: null,
      price: null,
      value: 'S$5,000.00',
      account: null,
      weight: null,
      day: null,
      qty: null,
      avgCost: null,
      uplBase: null,
      uplPct: null,
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.symbol).toBeUndefined()
    expect(result.price).toBeUndefined()
    expect(result.account).toBeUndefined()
    expect(result.weight).toBeUndefined()
    expect(result.day).toBeUndefined()
    expect(result.qty).toBeUndefined()
    expect(result.avgCost).toBeUndefined()
    expect(result.uplBase).toBeUndefined()
    expect(result.uplPct).toBeUndefined()
    expect(result.value).toBe('S$5,000.00')
  })

  it('includes uplBase and uplPct when present', () => {
    const rec: HoldingCtxRecord = {
      id: 'pos-3',
      value: 'S$18,520.00',
      uplBase: 'S$3,040.00',
      uplPct: '+19.62%',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.uplBase).toBe('S$3,040.00')
    expect(result.uplPct).toBe('+19.62%')
  })
})

// ── category ────────────────────────────────────────────────────────────────────

describe('buildCtxFields — category', () => {
  it('produces the expected keys for a full category row', () => {
    const rec: CategoryCtxRecord = {
      id: 'groceries',
      label: 'Groceries',
      assigned: 'S$800.00',
      activity: '−S$620.00',
      available: 'S$180.00',
      status: 'underfunded',
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.id).toBe('groceries')
    expect(result.label).toBe('Groceries')
    expect(result.assigned).toBe('S$800.00')
    expect(result.activity).toBe('−S$620.00')
    expect(result.available).toBe('S$180.00')
    expect(result.status).toBe('underfunded')
  })

  it('drops null status', () => {
    const rec: CategoryCtxRecord = {
      id: 'transport',
      label: 'Transport',
      assigned: 'S$200.00',
      activity: '−S$150.00',
      available: 'S$50.00',
      status: null,
    }
    const result = JSON.parse(buildCtxFields(rec))
    expect(result.status).toBeUndefined()
  })
})
